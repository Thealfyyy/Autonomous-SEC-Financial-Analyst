"""Contract tests for the PostgreSQL + pgvector dual-store schema."""

from pathlib import Path


def test_schema_contains_required_tables_and_views() -> None:
    schema_path = Path(__file__).resolve().parents[1] / "src" / "sec_analyst" / "db" / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")

    for required in (
        "CREATE EXTENSION IF NOT EXISTS vector;",
        "CREATE TABLE IF NOT EXISTS filings",
        "CREATE TABLE IF NOT EXISTS xbrl_facts",
        "CREATE OR REPLACE VIEW v_latest_xbrl_facts",
        "CREATE TABLE IF NOT EXISTS narrative_parents",
        "CREATE TABLE IF NOT EXISTS narrative_chunks",
        "VECTOR(1024)",
        "USING GIN (tsv)",
        "USING hnsw (embedding vector_cosine_ops)",
    ):
        assert required in sql, f"Missing required DDL in schema.sql: {required}"
