"""End-to-end SEC 10-K ingestion pipeline (EDGAR -> XBRL + Chunks -> Dual Store)."""

from __future__ import annotations

import argparse

from pydantic import BaseModel

from sec_analyst.config import Settings
from sec_analyst.db.repository import IngestionStore, PostgresIngestionStore
from sec_analyst.ingestion.edgar_client import EdgarClient
from sec_analyst.ingestion.embedder import Embedder, SentenceTransformerEmbedder
from sec_analyst.ingestion.section_chunker import (
    TARGET_10Q_SECTIONS,
    TARGET_SECTIONS,
    chunk_sections,
    extract_10k_sections,
)
from sec_analyst.ingestion.xbrl_parser import parse_company_facts


class IngestionSummary(BaseModel):
    """Structured summary returned after ingesting one ticker."""

    ticker: str
    filings_processed: int
    accession_numbers: list[str]
    xbrl_facts_written: int
    parents_written: int
    children_written: int
    sections_found: list[str]


def ingest_ticker(
    ticker: str,
    fiscal_years: set[int] | None,
    edgar_client: EdgarClient,
    store: IngestionStore,
    embedder: Embedder,
    include_10q: bool = False,
) -> IngestionSummary:
    """Download, parse, embed, and persist 10-K (and optional 10-Q) filings & XBRL facts."""
    form_types = ("10-K", "10-Q") if include_10q else ("10-K",)
    filings = edgar_client.get_filings(ticker, fiscal_years=fiscal_years, form_types=form_types)
    if not filings:
        return IngestionSummary(
            ticker=ticker.upper(),
            filings_processed=0,
            accession_numbers=[],
            xbrl_facts_written=0,
            parents_written=0,
            children_written=0,
            sections_found=[],
        )

    company_facts = edgar_client.get_company_facts(filings[0].cik)
    all_facts = parse_company_facts(company_facts, filings)

    total_facts = 0
    total_parents = 0
    total_children = 0
    sections_seen: set[str] = set()

    for filing in filings:
        store.upsert_filing(filing)

        filing_facts = [f for f in all_facts if f.accession_number == filing.accession_number]
        total_facts += store.upsert_xbrl_facts(filing_facts)

        html_path = edgar_client.download_filing_html(filing)
        html_text = html_path.read_text(encoding="utf-8", errors="ignore")
        target_secs = TARGET_10Q_SECTIONS if filing.form_type.startswith("10-Q") else TARGET_SECTIONS
        sections = extract_10k_sections(html_text, target_sections=target_secs)
        sections_seen.update(sections.keys())

        parents, children = chunk_sections(sections)
        embeddings = embedder.embed_texts([c.content for c in children])
        p_count, c_count = store.replace_narrative_chunks(filing, parents, children, embeddings)
        total_parents += p_count
        total_children += c_count

    return IngestionSummary(
        ticker=ticker.upper(),
        filings_processed=len(filings),
        accession_numbers=[f.accession_number for f in filings],
        xbrl_facts_written=total_facts,
        parents_written=total_parents,
        children_written=total_children,
        sections_found=sorted(sections_seen),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest SEC 10-K/10-Q filings into PostgreSQL.")
    parser.add_argument("--tickers", nargs="+", required=True, help="Ticker symbols (e.g. NVDA AAPL)")
    parser.add_argument("--years", nargs="+", type=int, default=[2022, 2023, 2024, 2025])
    parser.add_argument("--include-10q", action="store_true", help="Also ingest quarterly 10-Q filings.")
    args = parser.parse_args()

    settings = Settings()
    store = PostgresIngestionStore(settings.database_url)
    store.apply_schema()
    embedder = SentenceTransformerEmbedder(model_name=settings.embedding_model)

    with EdgarClient(
        user_agent=settings.sec_user_agent,
        cache_dir=settings.sec_cache_dir,
        max_rps=settings.sec_rate_limit_rps,
    ) as client:
        for ticker in args.tickers:
            summary = ingest_ticker(
                ticker,
                set(args.years),
                client,
                store,
                embedder,
                include_10q=args.include_10q,
            )
            print(summary.model_dump_json())


if __name__ == "__main__":
    main()
