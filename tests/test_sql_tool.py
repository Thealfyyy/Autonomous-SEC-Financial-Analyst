"""Offline unit & security tests for the read-only SQL validator."""

import pytest

from sec_analyst.tools.sql_tool import SqlValidationError, validate_readonly_sql


def test_validate_readonly_sql_allows_safe_select_and_cte() -> None:
    q1 = "SELECT ticker, fiscal_year, value FROM v_latest_xbrl_facts WHERE ticker = 'NVDA';"
    normalized = validate_readonly_sql(q1)
    assert normalized.endswith("LIMIT 100")

    q2 = """
    WITH rev AS (
        SELECT fiscal_year, value FROM v_latest_xbrl_facts
        WHERE ticker = 'AAPL' AND canonical_concept = 'revenue'
    )
    SELECT * FROM rev ORDER BY fiscal_year DESC LIMIT 10
    """
    normalized_cte = validate_readonly_sql(q2)
    assert "LIMIT 10" in normalized_cte

    q3 = "SELECT * FROM public.v_latest_xbrl_facts LIMIT 5;"
    assert "public.v_latest_xbrl_facts" in validate_readonly_sql(q3)


@pytest.mark.parametrize(
    "malicious_sql",
    [
        "",
        "DROP TABLE xbrl_facts;",
        "SELECT * FROM xbrl_facts; DROP TABLE filings;",
        "INSERT INTO filings VALUES ('a');",
        "UPDATE xbrl_facts SET value = 0;",
        "DELETE FROM xbrl_facts WHERE ticker = 'NVDA';",
        "/* comment */ ALTER TABLE filings ADD COLUMN hack TEXT;",
        "SELECT * FROM pg_catalog.pg_tables;",
        "SELECT * FROM narrative_chunks LIMIT 5;",
        "SELECT * FROM xbrl_facts LIMIT 9999;",
    ],
)
def test_validate_readonly_sql_blocks_unsafe_queries(malicious_sql: str) -> None:
    with pytest.raises(SqlValidationError):
        validate_readonly_sql(malicious_sql)
