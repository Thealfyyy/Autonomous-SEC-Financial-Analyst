"""Metadata-filtered Hybrid Search (pgvector + FTS) with RRF and Cross-Encoder Reranking."""

from __future__ import annotations

from typing import Protocol

import psycopg
from pgvector.psycopg import register_vector
from pydantic import BaseModel

from sec_analyst.ingestion.embedder import Embedder


class SearchCandidate(BaseModel):
    """Child chunk candidate returned from vector or full-text search."""

    chunk_id: int
    parent_id: int
    accession_number: str
    ticker: str
    fiscal_year: int
    item_section: str
    section_title: str | None = None
    filing_url: str
    child_content: str
    parent_content: str
    score: float = 0.0


class RetrievedContext(BaseModel):
    """Final deduplicated parent-level context with provenance URL and scores."""

    parent_id: int
    accession_number: str
    ticker: str
    fiscal_year: int
    item_section: str
    section_title: str | None = None
    filing_url: str
    content: str
    rrf_score: float
    rerank_score: float


class Reranker(Protocol):
    """Protocol for Cross-Encoder rerankers."""

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Return relevance scores aligned 1-to-1 with `passages`."""
        ...


class CrossEncoderReranker:
    """Lazy-loading Cross-Encoder reranker (defaults to BAAI/bge-reranker-v2-m3)."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name, device=self.device)
        return self._model

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        model = self._get_model()
        pairs = [(query, p) for p in passages]
        scores = model.predict(pairs, show_progress_bar=False)
        return [float(s) for s in scores]


def rrf_fuse(
    dense_results: list[SearchCandidate],
    sparse_results: list[SearchCandidate],
    k: int = 60,
) -> list[SearchCandidate]:
    """Combine dense and sparse rankings using Reciprocal Rank Fusion (RRF)."""
    scores: dict[int, float] = {}
    by_id: dict[int, SearchCandidate] = {}

    for rank, item in enumerate(dense_results, start=1):
        scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + 1.0 / (k + rank)
        by_id[item.chunk_id] = item

    for rank, item in enumerate(sparse_results, start=1):
        scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + 1.0 / (k + rank)
        by_id.setdefault(item.chunk_id, item)

    fused = []
    for chunk_id, rrf_score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        candidate = by_id[chunk_id].model_copy(update={"score": rrf_score})
        fused.append(candidate)
    return fused


def rerank_and_expand_parents(
    query: str,
    fused_candidates: list[SearchCandidate],
    reranker: Reranker,
    rerank_pool_size: int = 20,
    top_k_parents: int = 5,
) -> list[RetrievedContext]:
    """Rerank top child candidates and expand to unique parent paragraphs."""
    pool = fused_candidates[:rerank_pool_size]
    if not pool:
        return []

    rerank_scores = reranker.score(query, [c.child_content for c in pool])

    # Deduplicate by parent_id, keeping the highest-scoring child for each parent.
    best_by_parent: dict[int, RetrievedContext] = {}
    for candidate, r_score in zip(pool, rerank_scores, strict=True):
        existing = best_by_parent.get(candidate.parent_id)
        if existing is None or r_score > existing.rerank_score:
            best_by_parent[candidate.parent_id] = RetrievedContext(
                parent_id=candidate.parent_id,
                accession_number=candidate.accession_number,
                ticker=candidate.ticker,
                fiscal_year=candidate.fiscal_year,
                item_section=candidate.item_section,
                section_title=candidate.section_title,
                filing_url=candidate.filing_url,
                content=candidate.parent_content,
                rrf_score=candidate.score,
                rerank_score=r_score,
            )

    ranked_parents = sorted(
        best_by_parent.values(),
        key=lambda x: x.rerank_score,
        reverse=True,
    )
    return ranked_parents[:top_k_parents]


class PostgresHybridRetriever:
    """Metadata-filtered Hybrid Retriever backed by PostgreSQL + pgvector."""

    def __init__(
        self,
        database_url: str,
        embedder: Embedder,
        reranker: Reranker,
    ) -> None:
        self.database_url = database_url
        self.embedder = embedder
        self.reranker = reranker

    def search(
        self,
        query: str,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        item_sections: list[str] | None = None,
        candidate_limit: int = 25,
        top_k_parents: int = 5,
    ) -> list[RetrievedContext]:
        query_vec = self.embedder.embed_texts([query])[0]
        tickers_upper = [t.upper() for t in tickers] if tickers else None

        where_clauses = ["TRUE"]
        filter_params: list[object] = []
        if tickers_upper:
            where_clauses.append("nc.ticker = ANY(%s)")
            filter_params.append(tickers_upper)
        if fiscal_years:
            where_clauses.append("nc.fiscal_year = ANY(%s)")
            filter_params.append(fiscal_years)
        if item_sections:
            where_clauses.append("nc.item_section = ANY(%s)")
            filter_params.append(item_sections)

        where_sql = " AND ".join(where_clauses)

        dense_sql = f"""
            SELECT nc.id, nc.parent_id, nc.accession_number, nc.ticker, nc.fiscal_year,
                   nc.item_section, np.section_title, f.filing_url,
                   nc.content AS child_content, np.content AS parent_content,
                   1 - (nc.embedding <=> %s::vector) AS score
            FROM narrative_chunks nc
            JOIN narrative_parents np ON np.id = nc.parent_id
            JOIN filings f ON f.accession_number = nc.accession_number
            WHERE {where_sql} AND nc.embedding IS NOT NULL
            ORDER BY nc.embedding <=> %s::vector
            LIMIT %s;
        """

        sparse_sql = f"""
            SELECT nc.id, nc.parent_id, nc.accession_number, nc.ticker, nc.fiscal_year,
                   nc.item_section, np.section_title, f.filing_url,
                   nc.content AS child_content, np.content AS parent_content,
                   ts_rank_cd(nc.tsv, websearch_to_tsquery('english', %s)) AS score
            FROM narrative_chunks nc
            JOIN narrative_parents np ON np.id = nc.parent_id
            JOIN filings f ON f.accession_number = nc.accession_number
            WHERE {where_sql} AND nc.tsv @@ websearch_to_tsquery('english', %s)
            ORDER BY score DESC
            LIMIT %s;
        """

        with psycopg.connect(self.database_url) as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                cur.execute(dense_sql, (query_vec, *filter_params, query_vec, candidate_limit))
                dense_rows = [
                    SearchCandidate(
                        chunk_id=r[0],
                        parent_id=r[1],
                        accession_number=r[2],
                        ticker=r[3],
                        fiscal_year=r[4],
                        item_section=r[5],
                        section_title=r[6],
                        filing_url=r[7],
                        child_content=r[8],
                        parent_content=r[9],
                        score=float(r[10]),
                    )
                    for r in cur.fetchall()
                ]

                cur.execute(sparse_sql, (query, *filter_params, query, candidate_limit))
                sparse_rows = [
                    SearchCandidate(
                        chunk_id=r[0],
                        parent_id=r[1],
                        accession_number=r[2],
                        ticker=r[3],
                        fiscal_year=r[4],
                        item_section=r[5],
                        section_title=r[6],
                        filing_url=r[7],
                        child_content=r[8],
                        parent_content=r[9],
                        score=float(r[10]),
                    )
                    for r in cur.fetchall()
                ]

        fused = rrf_fuse(dense_rows, sparse_rows)
        return rerank_and_expand_parents(
            query=query,
            fused_candidates=fused,
            reranker=self.reranker,
            top_k_parents=top_k_parents,
        )
