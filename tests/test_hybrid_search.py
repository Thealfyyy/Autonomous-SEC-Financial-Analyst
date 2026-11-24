"""Offline unit tests for Hybrid Search (RRF) and Cross-Encoder parent expansion."""

from sec_analyst.retrieval.hybrid_search import (
    SearchCandidate,
    rerank_and_expand_parents,
    rrf_fuse,
)


class FakeReranker:
    """Deterministic reranker scoring passages containing 'foundry' highest."""

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [0.95 if "foundry" in p.lower() else 0.25 for p in passages]


def _make_candidate(chunk_id: int, parent_id: int, text: str) -> SearchCandidate:
    return SearchCandidate(
        chunk_id=chunk_id,
        parent_id=parent_id,
        accession_number="0001045810-24-000029",
        ticker="NVDA",
        fiscal_year=2024,
        item_section="Item 1A",
        section_title="Risk Factors",
        filing_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581024000029/nvda.htm",
        child_content=text,
        parent_content=f"FULL PARENT {parent_id}: {text}",
    )


def test_rrf_and_rerank_deduplicates_to_unique_parents() -> None:
    c1 = _make_candidate(1, 10, "General market competition.")
    c2 = _make_candidate(2, 20, "Third-party foundry capacity constraints.")
    c3 = _make_candidate(3, 20, "Another snippet from the same foundry parent.")

    fused = rrf_fuse(dense_results=[c1, c2], sparse_results=[c2, c3, c1])
    # c2 appears in both lists, so it should rank first under RRF
    assert fused[0].chunk_id == 2

    contexts = rerank_and_expand_parents(
        query="supply chain foundry risks",
        fused_candidates=fused,
        reranker=FakeReranker(),
        top_k_parents=5,
    )
    # Parent 20 had two child chunks (c2, c3), but must appear once after expansion
    assert [ctx.parent_id for ctx in contexts] == [20, 10]
    assert contexts[0].rerank_score == 0.95
    assert contexts[0].content.startswith("FULL PARENT 20:")
