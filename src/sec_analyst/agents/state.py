"""Typed state and structured schemas for the 4-node LangGraph analyst workflow."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field

from sec_analyst.retrieval.hybrid_search import RetrievedContext
from sec_analyst.tools.filing_diff import FilingDiffResult
from sec_analyst.tools.financial_math import MetricResult
from sec_analyst.tools.sql_tool import SqlResult


class MathOperation(BaseModel):
    """Deterministic Python calculation requested by the Planner/Quant worker."""

    op: Literal["yoy_growth", "margin", "cagr", "ratio", "free_cash_flow"]
    metric_name: str
    args: dict[str, float | int | str]


class QualitativeTask(BaseModel):
    """Metadata-filtered qualitative retrieval sub-task."""

    query: str
    item_sections: list[Literal["Item 1", "Item 1A", "Item 1C", "Item 7", "Item 8"]] = Field(
        default_factory=lambda: ["Item 1A", "Item 7"]
    )


class QueryPlan(BaseModel):
    """Structured output of the Planner / Router node."""

    tickers: list[str] = Field(default_factory=list)
    fiscal_years: list[int] = Field(default_factory=list)
    quantitative_queries: list[str] = Field(default_factory=list)
    math_operations: list[MathOperation] = Field(default_factory=list)
    qualitative_queries: list[QualitativeTask] = Field(default_factory=list)
    needs_diff: bool = False
    diff_section: str = "Item 1A"
    abstain: bool = False
    abstain_reason: str = ""


class Citation(BaseModel):
    """Auditable source citation pointing to an indexed SEC filing."""

    ticker: str
    fiscal_year: int
    item_section: str
    accession_number: str
    filing_url: str


class AuditResult(BaseModel):
    """Deterministic verification outcome from the Auditor node."""

    passed: bool
    issues: list[str] = Field(default_factory=list)
    unverified_numbers: list[str] = Field(default_factory=list)
    missing_citations: list[str] = Field(default_factory=list)


class AnalystState(TypedDict, total=False):
    """Shared LangGraph state flowing through Planner -> Workers -> Synthesizer -> Auditor."""

    question: str
    indexed_tickers: list[str]
    indexed_years: list[int]
    indexed_periods: list[str]
    plan: QueryPlan | None
    sql_results: list[SqlResult]
    computed_metrics: list[MetricResult]
    retrieved_contexts: list[RetrievedContext]
    diff_results: list[FilingDiffResult]
    draft_answer: str
    citations: list[Citation]
    audit_result: AuditResult | None
    retry_count: int
    status: Literal["ok", "insufficient_evidence"]
    abstain_reason: str
    events: list[dict[str, Any]]
