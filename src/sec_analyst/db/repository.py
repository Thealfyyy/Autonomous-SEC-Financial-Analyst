"""PostgreSQL + pgvector repository for filings, XBRL facts, and narrative chunks."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import psycopg
from pgvector.psycopg import register_vector

from sec_analyst.ingestion.edgar_client import FilingMetadata
from sec_analyst.ingestion.section_chunker import ChildChunk, ParentChunk
from sec_analyst.ingestion.xbrl_parser import XbrlFactRecord

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


class IngestionStore(Protocol):
    """Storage contract used by the ingestion pipeline (real Postgres or test fake)."""

    def upsert_filing(self, filing: FilingMetadata) -> None: ...

    def upsert_xbrl_facts(self, facts: list[XbrlFactRecord]) -> int: ...

    def replace_narrative_chunks(
        self,
        filing: FilingMetadata,
        parents: list[ParentChunk],
        children: list[ChildChunk],
        embeddings: list[list[float]],
    ) -> tuple[int, int]: ...


class PostgresIngestionStore:
    """Transactional PostgreSQL + pgvector persistence layer."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self.database_url)
        register_vector(conn)
        return conn

    def apply_schema(self) -> None:
        """Execute schema.sql idempotently."""
        ddl = SCHEMA_PATH.read_text(encoding="utf-8")
        with psycopg.connect(self.database_url, autocommit=True) as conn:
            conn.execute(ddl)

    def upsert_filing(self, filing: FilingMetadata) -> None:
        sql = """
            INSERT INTO filings (
                accession_number, cik, ticker, company_name, form_type,
                fiscal_year, fiscal_period, filing_date, period_end_date, primary_document,
                filing_url, is_amendment
            )
            VALUES (
                %(accession_number)s, %(cik)s, %(ticker)s, %(company_name)s, %(form_type)s,
                %(fiscal_year)s, %(fiscal_period)s, %(filing_date)s, %(period_end_date)s, %(primary_document)s,
                %(filing_url)s, %(is_amendment)s
            )
            ON CONFLICT (accession_number) DO UPDATE SET
                fiscal_period = EXCLUDED.fiscal_period,
                filing_date = EXCLUDED.filing_date,
                period_end_date = EXCLUDED.period_end_date,
                filing_url = EXCLUDED.filing_url,
                is_amendment = EXCLUDED.is_amendment;
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, filing.model_dump())

    def upsert_xbrl_facts(self, facts: list[XbrlFactRecord]) -> int:
        if not facts:
            return 0
        sql = """
            INSERT INTO xbrl_facts (
                accession_number, ticker, fiscal_year, fiscal_period, period_end_date,
                raw_tag, canonical_concept, value, unit, decimals, frame
            )
            VALUES (
                %(accession_number)s, %(ticker)s, %(fiscal_year)s, %(fiscal_period)s, %(period_end_date)s,
                %(raw_tag)s, %(canonical_concept)s, %(value)s, %(unit)s, %(decimals)s, %(frame)s
            )
            ON CONFLICT (accession_number, raw_tag, period_end_date, fiscal_period, unit) DO UPDATE SET
                canonical_concept = EXCLUDED.canonical_concept,
                value = EXCLUDED.value,
                decimals = EXCLUDED.decimals,
                frame = EXCLUDED.frame;
        """
        rows = [f.model_dump() for f in facts]
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(sql, rows)
        return len(rows)

    def replace_narrative_chunks(
        self,
        filing: FilingMetadata,
        parents: list[ParentChunk],
        children: list[ChildChunk],
        embeddings: list[list[float]],
    ) -> tuple[int, int]:
        if len(children) != len(embeddings):
            raise ValueError("children and embeddings must have identical length.")

        with self._connect() as conn, conn.cursor() as cur:
            # Idempotent re-ingestion: clear prior parents/chunks for this accession_number
            cur.execute(
                "DELETE FROM narrative_parents WHERE accession_number = %s;",
                (filing.accession_number,),
            )

            parent_id_map: dict[tuple[str, int], int] = {}
            for p in parents:
                cur.execute(
                    """
                    INSERT INTO narrative_parents (
                        accession_number, ticker, fiscal_year,
                        item_section, section_title, parent_index, content
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id;
                    """,
                    (
                        filing.accession_number,
                        filing.ticker,
                        filing.fiscal_year,
                        p.item_section,
                        p.section_title,
                        p.parent_index,
                        p.content,
                    ),
                )
                row = cur.fetchone()
                assert row is not None
                parent_id_map[(p.item_section, p.parent_index)] = int(row[0])

            child_rows = [
                (
                    parent_id_map[(c.item_section, c.parent_index)],
                    filing.accession_number,
                    filing.ticker,
                    filing.fiscal_year,
                    c.item_section,
                    c.chunk_index,
                    c.content,
                    emb,
                )
                for c, emb in zip(children, embeddings, strict=True)
            ]
            cur.executemany(
                """
                INSERT INTO narrative_chunks (
                    parent_id, accession_number, ticker, fiscal_year,
                    item_section, chunk_index, content, embedding
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                """,
                child_rows,
            )

        return len(parents), len(children)
