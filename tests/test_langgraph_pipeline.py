"""Offline tests for the 4-node LangGraph workflow and deterministic Auditor."""

from typing import Any

from sec_analyst.agents.graph import build_analyst_graph
from sec_analyst.retrieval.hybrid_search import RetrievedContext
from sec_analyst.tools.sql_tool import SqlResult


class ScriptedLLMClient:
    """Deterministic scripted LLM client for offline graph & retry testing."""

    def __init__(self, json_responses: list[dict[str, Any]]) -> None:
        self._responses = list(json_responses)
        self.calls = 0

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = self._responses[self.calls]
        self.calls += 1
        return resp

    def chat_text(self, system_prompt: str, user_prompt: str) -> str:
        return ""


class FakeSqlExecutor:
    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> SqlResult:
        return SqlResult(
            sql=sql,
            columns=["ticker", "fiscal_year", "value", "unit", "accession_number"],
            rows=[
                {
                    "ticker": "NVDA",
                    "fiscal_year": 2024,
                    "value": 60922000000,
                    "unit": "USD",
                    "accession_number": "0001045810-24-000029",
                },
                {
                    "ticker": "NVDA",
                    "fiscal_year": 2023,
                    "value": 26974000000,
                    "unit": "USD",
                    "accession_number": "0001045810-23-000017",
                },
            ],
            row_count=2,
        )


class FakeRetriever:
    def search(
        self,
        query: str,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        item_sections: list[str] | None = None,
        candidate_limit: int = 25,
        top_k_parents: int = 5,
    ) -> list[RetrievedContext]:
        return [
            RetrievedContext(
                parent_id=1,
                accession_number="0001045810-24-000029",
                ticker="NVDA",
                fiscal_year=2024,
                item_section="Item 1A",
                section_title="Risk Factors",
                filing_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581024000029/nvda.htm",
                content="Supply chain concentration in advanced semiconductor packaging poses risk.",
                rrf_score=0.03,
                rerank_score=0.92,
            )
        ]


def _nvda_plan() -> dict[str, Any]:
    return {
        "tickers": ["NVDA"],
        "fiscal_years": [2023, 2024],
        "quantitative_queries": [
            "SELECT ticker, fiscal_year, value, unit, accession_number FROM v_latest_xbrl_facts WHERE ticker = 'NVDA'"
        ],
        "math_operations": [
            {
                "op": "yoy_growth",
                "metric_name": "revenue_yoy",
                "args": {"current": 60922000000, "previous": 26974000000},
            }
        ],
        "qualitative_queries": [{"query": "supply chain risks", "item_sections": ["Item 1A"]}],
        "abstain": False,
        "abstain_reason": "",
    }


def _valid_citation() -> dict[str, Any]:
    return {
        "ticker": "NVDA",
        "fiscal_year": 2024,
        "item_section": "Item 1A",
        "accession_number": "0001045810-24-000029",
        "filing_url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581024000029/nvda.htm",
    }


def test_graph_happy_path_passes_auditor() -> None:
    llm = ScriptedLLMClient(
        [
            _nvda_plan(),
            {
                "answer": "NVIDIA FY2024 revenue reached $60.92B (+125.85% YoY from $26.97B), with supply chain packaging risks noted in Item 1A.",
                "citations": [_valid_citation()],
            },
        ]
    )
    graph = build_analyst_graph(llm=llm, sql_executor=FakeSqlExecutor(), retriever=FakeRetriever())
    result = graph.invoke(
        {
            "question": "What was NVDA FY2024 revenue growth and supply chain risk?",
            "indexed_tickers": ["NVDA"],
            "indexed_years": [2023, 2024],
            "events": [],
        }
    )

    assert result["status"] == "ok"
    assert result["audit_result"].passed is True
    assert result["retry_count"] == 0


def test_graph_abstains_on_unindexed_ticker() -> None:
    llm = ScriptedLLMClient(
        [
            {
                "tickers": ["UBER"],
                "fiscal_years": [2024],
                "quantitative_queries": [],
                "math_operations": [],
                "qualitative_queries": [],
                "abstain": False,
                "abstain_reason": "",
            }
        ]
    )
    graph = build_analyst_graph(llm=llm, sql_executor=FakeSqlExecutor(), retriever=FakeRetriever())
    result = graph.invoke(
        {
            "question": "What was Uber revenue in 2024?",
            "indexed_tickers": ["NVDA", "AAPL"],
            "indexed_years": [2023, 2024],
            "events": [],
        }
    )

    assert result["status"] == "insufficient_evidence"
    assert "UBER" in result["draft_answer"]


def test_auditor_catches_hallucinated_number_and_retries_once() -> None:
    llm = ScriptedLLMClient(
        [
            _nvda_plan(),
            # First draft hallucinates 999.99%
            {
                "answer": "NVIDIA revenue grew 999.99% in FY2024.",
                "citations": [_valid_citation()],
            },
            # Retry fixes to verified +125.85%
            {
                "answer": "NVIDIA revenue grew +125.85% in FY2024 to $60.92B.",
                "citations": [_valid_citation()],
            },
        ]
    )
    graph = build_analyst_graph(llm=llm, sql_executor=FakeSqlExecutor(), retriever=FakeRetriever())
    result = graph.invoke(
        {
            "question": "What was NVDA FY2024 revenue growth?",
            "indexed_tickers": ["NVDA"],
            "indexed_years": [2023, 2024],
            "events": [],
        }
    )

    assert result["status"] == "ok"
    assert result["retry_count"] == 1
    assert result["audit_result"].passed is True


def test_auditor_abstains_if_hallucination_persists_after_max_retry() -> None:
    llm = ScriptedLLMClient(
        [
            _nvda_plan(),
            {"answer": "NVIDIA revenue grew 888.88%.", "citations": [_valid_citation()]},
            {"answer": "NVIDIA revenue still grew 777.77%.", "citations": [_valid_citation()]},
        ]
    )
    graph = build_analyst_graph(llm=llm, sql_executor=FakeSqlExecutor(), retriever=FakeRetriever())
    result = graph.invoke(
        {
            "question": "What was NVDA FY2024 revenue growth?",
            "indexed_tickers": ["NVDA"],
            "indexed_years": [2023, 2024],
            "events": [],
        }
    )

    assert result["status"] == "insufficient_evidence"
    assert result["retry_count"] == 1
    assert "Insufficient evidence" in result["draft_answer"]


def test_auto_compute_metrics_computes_margins_and_ratios() -> None:
    from decimal import Decimal

    from sec_analyst.agents.graph import _auto_compute_metrics
    from sec_analyst.tools.sql_tool import SqlResult

    sql_res = SqlResult(
        sql="SELECT ...",
        columns=["ticker", "fiscal_year", "canonical_concept", "value", "unit"],
        rows=[
            {"ticker": "AAPL", "fiscal_year": 2024, "canonical_concept": "revenue", "value": Decimal(391035000000), "unit": "USD"},
            {"ticker": "AAPL", "fiscal_year": 2024, "canonical_concept": "research_and_development", "value": Decimal(31370000000), "unit": "USD"},
            {"ticker": "MSFT", "fiscal_year": 2024, "canonical_concept": "revenue", "value": Decimal(245122000000), "unit": "USD"},
            {"ticker": "MSFT", "fiscal_year": 2024, "canonical_concept": "research_and_development", "value": Decimal(29510000000), "unit": "USD"},
        ],
        row_count=4,
    )
    metrics = []
    _auto_compute_metrics([sql_res], metrics)
    by_name = {m.name: m for m in metrics}
    assert str(by_name["AAPL_FY2024_rd_pct_of_revenue"].value) == "8.02"
    assert str(by_name["MSFT_FY2024_rd_pct_of_revenue"].value) == "12.04"


def test_format_sql_row_for_llm_prevents_unit_scaling_errors() -> None:
    from sec_analyst.agents.graph import _format_sql_row_for_llm

    row_b = _format_sql_row_for_llm({"ticker": "MSFT", "value": 245122000000, "unit": "USD"})
    assert row_b["formatted_value"] == "$245.12 billion ($245,122,000,000)"

    row_m = _format_sql_row_for_llm({"ticker": "AMD", "value": -450000000, "unit": "USD"})
    assert row_m["formatted_value"] == "-$450.00 million (-$450,000,000)"

    row_eps = _format_sql_row_for_llm({"ticker": "NVDA", "value": 11.93, "unit": "USD/shares"})
    assert row_eps["formatted_value"] == "$11.93 per share"


def test_quarterly_query_abstains_without_10q_and_routes_when_indexed() -> None:
    llm_unused = ScriptedLLMClient([])
    graph_fy_only = build_analyst_graph(
        llm=llm_unused,
        sql_executor=FakeSqlExecutor(),
        retriever=FakeRetriever(),
    )
    abstained = graph_fy_only.invoke(
        {
            "question": "What was NVDA Q2 2024 quarterly revenue from its 10-Q?",
            "indexed_tickers": ["NVDA"],
            "indexed_years": [2024],
            "indexed_periods": ["FY"],
            "events": [],
        }
    )
    assert abstained["status"] == "insufficient_evidence"
    assert "no quarterly 10-Q filings" in abstained["draft_answer"]

    llm_q2 = ScriptedLLMClient(
        [
            _nvda_plan(),
            {
                "answer": "NVIDIA Q2 2024 revenue was $60.92B.",
                "citations": [_valid_citation()],
            },
        ]
    )
    graph_with_q = build_analyst_graph(
        llm=llm_q2,
        sql_executor=FakeSqlExecutor(),
        retriever=FakeRetriever(),
    )
    passed = graph_with_q.invoke(
        {
            "question": "What was NVDA Q2 2024 quarterly revenue from its 10-Q?",
            "indexed_tickers": ["NVDA"],
            "indexed_years": [2023, 2024],
            "indexed_periods": ["FY", "Q1", "Q2", "Q3"],
            "events": [],
        }
    )
    assert passed["status"] == "ok"
