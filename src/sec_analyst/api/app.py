"""FastAPI service exposing /health, /analyze (SSE), and /ingest."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sec_analyst import __version__
from sec_analyst.agents.graph import RetrieverProtocol, build_analyst_graph
from sec_analyst.agents.llm_client import LLMClient, OpenAICompatibleLLMClient
from sec_analyst.config import Settings
from sec_analyst.db.repository import IngestionStore, PostgresIngestionStore
from sec_analyst.ingestion.edgar_client import EdgarClient
from sec_analyst.ingestion.embedder import Embedder, SentenceTransformerEmbedder
from sec_analyst.ingestion.pipeline import IngestionSummary, ingest_ticker
from sec_analyst.observability import TraceRecorder
from sec_analyst.retrieval.hybrid_search import CrossEncoderReranker, PostgresHybridRetriever
from sec_analyst.tools.sql_tool import PostgresReadOnlySqlExecutor, SqlExecutor


class AnalyzeRequest(BaseModel):
    question: str = Field(..., min_length=3)
    indexed_tickers: list[str] | None = None
    indexed_years: list[int] | None = None


class IngestRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    fiscal_years: list[int] = Field(default_factory=lambda: [2022, 2023, 2024, 2025])
    include_10q: bool = False


def _format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def create_app(
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    sql_executor: SqlExecutor | None = None,
    retriever: RetrieverProtocol | None = None,
    ingestion_store: IngestionStore | None = None,
    embedder: Embedder | None = None,
    edgar_client: EdgarClient | None = None,
) -> FastAPI:
    """Create the FastAPI app with injectable dependencies for clean offline testing."""
    cfg = settings or Settings()
    app = FastAPI(title="Autonomous SEC Financial Analyst", version=__version__)
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    _cached: dict[str, Any] = {}

    def _get_llm() -> LLMClient:
        if llm_client is not None:
            return llm_client
        if "llm" not in _cached:
            _cached["llm"] = OpenAICompatibleLLMClient(
                base_url=cfg.llm_base_url,
                model=cfg.llm_model,
            )
        return _cached["llm"]

    def _get_sql_executor() -> SqlExecutor:
        if sql_executor is not None:
            return sql_executor
        if "sql_executor" not in _cached:
            _cached["sql_executor"] = PostgresReadOnlySqlExecutor(cfg.database_readonly_url)
        return _cached["sql_executor"]

    def _get_embedder() -> Embedder:
        if embedder is not None:
            return embedder
        if "embedder" not in _cached:
            _cached["embedder"] = SentenceTransformerEmbedder(model_name=cfg.embedding_model)
        return _cached["embedder"]

    def _get_retriever() -> RetrieverProtocol:
        if retriever is not None:
            return retriever
        if "retriever" not in _cached:
            _cached["retriever"] = PostgresHybridRetriever(
                database_url=cfg.database_url,
                embedder=_get_embedder(),
                reranker=CrossEncoderReranker(model_name=cfg.reranker_model),
            )
        return _cached["retriever"]

    def _get_ingestion_store() -> IngestionStore:
        if ingestion_store is not None:
            return ingestion_store
        if "ingestion_store" not in _cached:
            _cached["ingestion_store"] = PostgresIngestionStore(cfg.database_url)
        return _cached["ingestion_store"]

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    def _load_indexed_corpus() -> tuple[list[str], list[int], list[str]]:
        sql_exec = _get_sql_executor()
        try:
            res = sql_exec.execute(
                "SELECT DISTINCT ticker, fiscal_year, fiscal_period FROM filings ORDER BY ticker, fiscal_year"
            )
            periods = sorted({str(r["fiscal_period"]).upper() for r in res.rows if r.get("fiscal_period")})
        except Exception:  # noqa: BLE001
            res = sql_exec.execute(
                "SELECT DISTINCT ticker, fiscal_year FROM filings ORDER BY ticker, fiscal_year"
            )
            periods = ["FY"]
        tickers = sorted({str(r["ticker"]).upper() for r in res.rows if r.get("ticker")})
        years = sorted({int(r["fiscal_year"]) for r in res.rows if r.get("fiscal_year") is not None})
        return tickers, years, (periods or ["FY"])

    def _load_section_paragraphs(ticker: str, item_section: str, fiscal_year: int) -> list[str]:
        if retriever is not None:
            return []
        with psycopg.connect(cfg.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT content
                FROM narrative_parents
                WHERE ticker = %s AND item_section = %s AND fiscal_year = %s
                ORDER BY parent_index ASC
                """,
                (ticker.upper(), item_section, fiscal_year),
            )
            return [str(row[0]) for row in cur.fetchall()]

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "version": __version__,
            "model": cfg.llm_model,
        }

    @app.get("/corpus")
    def corpus() -> dict[str, Any]:
        tickers, years, periods = _load_indexed_corpus()
        return {"tickers": tickers, "fiscal_years": years, "fiscal_periods": periods}

    @app.post("/analyze")
    def analyze(req: AnalyzeRequest) -> StreamingResponse:
        llm = _get_llm()
        sql_exec = _get_sql_executor()
        ret = _get_retriever()
        trace = TraceRecorder(trace_name="sec-analyst-analyze")
        graph = build_analyst_graph(
            llm=llm,
            sql_executor=sql_exec,
            retriever=ret,
            diff_loader=_load_section_paragraphs,
            trace=trace,
        )

        tickers = req.indexed_tickers
        years = req.indexed_years
        periods = ["FY"]
        if tickers is None or years is None:
            db_tickers, db_years, db_periods = _load_indexed_corpus()
            if tickers is None:
                tickers = db_tickers
            if years is None:
                years = db_years
            periods = db_periods

        def event_stream() -> Iterator[str]:
            initial_state = {
                "question": req.question,
                "indexed_tickers": tickers or [],
                "indexed_years": years or [],
                "indexed_periods": periods,
                "events": [],
            }
            final_state: dict[str, Any] = {}
            try:
                for step in graph.stream(initial_state, stream_mode="updates"):
                    for node_name, node_output in step.items():
                        final_state.update(node_output)
                        node_status = node_output.get("status", "running")
                        if (
                            node_name == "auditor"
                            and node_output.get("audit_result") is not None
                            and not node_output["audit_result"].passed
                            and node_output.get("retry_count", 0) > 0
                        ):
                            node_status = "retrying"
                        yield _format_sse(
                            "node_update",
                            {"node": node_name, "status": node_status},
                        )
            except Exception as exc:  # noqa: BLE001
                yield _format_sse("error", {"detail": str(exc)})
                return

            payload = {
                "status": final_state.get("status", "insufficient_evidence"),
                "answer": final_state.get("draft_answer", ""),
                "citations": [c.model_dump() for c in final_state.get("citations", [])],
                "sql_results": [r.model_dump() for r in final_state.get("sql_results", [])],
                "computed_metrics": [
                    m.model_dump(mode="json") for m in final_state.get("computed_metrics", [])
                ],
                "retrieved_contexts": [
                    c.model_dump() for c in final_state.get("retrieved_contexts", [])
                ],
                "diff_results": [
                    d.model_dump() for d in final_state.get("diff_results", [])
                ],
                "audit_result": (
                    final_state["audit_result"].model_dump()
                    if final_state.get("audit_result")
                    else None
                ),
                "spans": [s.model_dump() for s in trace.spans],
            }
            yield _format_sse("final", payload)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/ingest", response_model=IngestionSummary)
    def ingest(req: IngestRequest) -> IngestionSummary:
        store = _get_ingestion_store()
        emb = _get_embedder()

        if edgar_client is not None:
            return ingest_ticker(
                req.ticker,
                set(req.fiscal_years),
                edgar_client,
                store,
                emb,
                include_10q=req.include_10q,
            )

        with EdgarClient(
            user_agent=cfg.sec_user_agent,
            cache_dir=cfg.sec_cache_dir,
            max_rps=cfg.sec_rate_limit_rps,
        ) as client:
            return ingest_ticker(
                req.ticker,
                set(req.fiscal_years),
                client,
                store,
                emb,
                include_10q=req.include_10q,
            )

    return app
