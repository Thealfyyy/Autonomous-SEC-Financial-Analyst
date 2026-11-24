"""Adversarial security & guardrail test suite for offline CI."""

import pytest

from sec_analyst.agents.auditor import verify_answer
from sec_analyst.agents.state import Citation
from sec_analyst.tools.financial_math import ratio
from sec_analyst.tools.sql_tool import SqlResult, SqlValidationError, validate_readonly_sql


@pytest.mark.parametrize(
    "attack_sql",
    [
        "SELECT * FROM v_latest_xbrl_facts; INSERT INTO filings VALUES ('x');",
        "SELECT * FROM v_latest_xbrl_facts UNION SELECT * FROM pg_shadow;",
        "WITH t AS (SELECT 1) DELETE FROM xbrl_facts;",
    ],
)
def test_sql_injection_blocked(attack_sql: str) -> None:
    with pytest.raises(SqlValidationError):
        validate_readonly_sql(attack_sql)


def test_auditor_rejects_ungrounded_citation_accession() -> None:
    fake_citation = Citation(
        ticker="NVDA",
        fiscal_year=2024,
        item_section="Item 1A",
        accession_number="9999999999-99-999999",
        filing_url="https://www.sec.gov/fake",
    )
    audit = verify_answer(
        draft_answer="NVDA revenue was $60.92B.",
        citations=[fake_citation],
        sql_results=[
            SqlResult(
                sql="SELECT 1",
                columns=["value", "accession_number"],
                rows=[{"value": 60922000000, "accession_number": "0001045810-24-000029"}],
                row_count=1,
            )
        ],
        computed_metrics=[],
        retrieved_contexts=[],
        indexed_years=[2024],
    )
    assert audit.passed is False
    assert len(audit.missing_citations) == 1


def test_financial_math_rejects_non_finite_inputs() -> None:
    with pytest.raises(ValueError, match="Non-finite"):
        ratio(float("nan"), 100)


def test_auditor_accepts_verbatim_numbers_from_filing_diffs() -> None:
    from sec_analyst.tools.filing_diff import FilingDiffResult

    diff = FilingDiffResult(
        ticker="TSLA",
        item_section="Item 7",
        previous_year=2023,
        current_year=2024,
        unchanged_count=10,
        added_paragraphs=["Vehicle deliveries totaled 1,789,226 units in 2024."],
        removed_paragraphs=[],
        modified_paragraphs=[],
    )
    audit = verify_answer(
        draft_answer="TSLA reported 1,789,226 vehicle deliveries in FY2024 Item 7.",
        citations=[],
        sql_results=[],
        computed_metrics=[],
        retrieved_contexts=[],
        indexed_years=[2023, 2024],
        diff_results=[diff],
    )
    assert audit.passed is True
