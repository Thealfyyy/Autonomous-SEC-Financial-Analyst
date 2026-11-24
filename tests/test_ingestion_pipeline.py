"""Offline end-to-end ingestion integrity tests."""

from pathlib import Path

import httpx

from sec_analyst.ingestion.edgar_client import EdgarClient, FilingMetadata
from sec_analyst.ingestion.pipeline import ingest_ticker
from sec_analyst.ingestion.section_chunker import ChildChunk, ParentChunk
from sec_analyst.ingestion.xbrl_parser import XbrlFactRecord

SAMPLE_HTML = """
<html><body>
  <h2>ITEM 1A. RISK FACTORS</h2>
  <p>Supply chain concentration in advanced semiconductor packaging poses material operational risk.</p>
  <h2>ITEM 1C. CYBERSECURITY</h2>
  <p>Our Board Audit Committee oversees cybersecurity risk management and incident response.</p>
  <h2>ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS</h2>
  <p>Revenue grew year over year driven by accelerated computing demand.</p>
</body></html>
"""


class FakeEmbedder:
    """Deterministic 1024-dim embedder for offline CI tests."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.01] * 1024 for _ in texts]


class InMemoryStore:
    """In-memory implementation of IngestionStore for integrity verification."""

    def __init__(self) -> None:
        self.filings: dict[str, FilingMetadata] = {}
        self.facts: dict[tuple[str, str, str, str], XbrlFactRecord] = {}
        self.parents: list[tuple[int, str, ParentChunk]] = []
        self.children: list[tuple[int, str, ChildChunk, list[float]]] = []
        self._next_parent_id = 1

    def upsert_filing(self, filing: FilingMetadata) -> None:
        self.filings[filing.accession_number] = filing

    def upsert_xbrl_facts(self, facts: list[XbrlFactRecord]) -> int:
        for f in facts:
            key = (f.accession_number, f.raw_tag, str(f.period_end_date), f.unit)
            self.facts[key] = f
        return len(facts)

    def replace_narrative_chunks(
        self,
        filing: FilingMetadata,
        parents: list[ParentChunk],
        children: list[ChildChunk],
        embeddings: list[list[float]],
    ) -> tuple[int, int]:
        self.parents = [p for p in self.parents if p[1] != filing.accession_number]
        self.children = [c for c in self.children if c[1] != filing.accession_number]

        id_map: dict[tuple[str, int], int] = {}
        for p in parents:
            pid = self._next_parent_id
            self._next_parent_id += 1
            id_map[(p.item_section, p.parent_index)] = pid
            self.parents.append((pid, filing.accession_number, p))

        for c, emb in zip(children, embeddings, strict=True):
            pid = id_map[(c.item_section, c.parent_index)]
            self.children.append((pid, filing.accession_number, c, emb))

        return len(parents), len(children)


def _mock_edgar_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}})
        if "submissions" in url:
            return httpx.Response(
                200,
                json={
                    "cik": "0001045810",
                    "filings": {
                        "recent": {
                            "form": ["10-K"],
                            "accessionNumber": ["0001045810-24-000029"],
                            "filingDate": ["2024-02-21"],
                            "reportDate": ["2024-01-28"],
                            "primaryDocument": ["nvda-20240128.htm"],
                        }
                    },
                },
            )
        if "companyfacts" in url:
            return httpx.Response(
                200,
                json={
                    "cik": 1045810,
                    "facts": {
                        "us-gaap": {
                            "Revenues": {
                                "units": {
                                    "USD": [
                                        {
                                            "accn": "0001045810-24-000029",
                                            "form": "10-K",
                                            "fp": "FY",
                                            "start": "2023-01-30",
                                            "end": "2024-01-28",
                                            "val": 60922000000,
                                        }
                                    ]
                                }
                            }
                        }
                    },
                },
            )
        if url.endswith("nvda-20240128.htm"):
            return httpx.Response(200, text=SAMPLE_HTML)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_ingest_ticker_integrity_and_idempotency(tmp_path: Path) -> None:
    edgar = EdgarClient(
        user_agent="AutonomousSECAnalyst/0.1 test@example.com",
        cache_dir=tmp_path,
        max_rps=10.0,
        http_client=httpx.Client(transport=_mock_edgar_transport()),
    )
    store = InMemoryStore()
    embedder = FakeEmbedder()

    summary = ingest_ticker("NVDA", {2024}, edgar, store, embedder)
    assert summary.filings_processed == 1
    assert summary.xbrl_facts_written == 1
    assert summary.sections_found == ["Item 1A", "Item 1C", "Item 7"]
    assert summary.parents_written >= 3
    assert summary.children_written >= 3

    # Verify idempotency: re-running must not create duplicate filings, facts, or chunks
    summary2 = ingest_ticker("NVDA", {2024}, edgar, store, embedder)
    assert len(store.filings) == 1
    assert len(store.facts) == 1
    assert len(store.parents) == summary2.parents_written
    assert len(store.children) == summary2.children_written
    assert all(len(emb) == 1024 for _, _, _, emb in store.children)
