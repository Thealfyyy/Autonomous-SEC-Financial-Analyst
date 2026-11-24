"""FinanceBench evaluation & controlled ablation runner."""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

_NUM_RE = re.compile(r"([+\-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(%|B|M|K)?", re.IGNORECASE)
_MULTIPLIERS = {"b": 1e9, "m": 1e6, "k": 1e3}


class EvalCase(BaseModel):
    """Single FinanceBench-style evaluation item."""

    case_id: str
    ticker: str
    fiscal_year: int
    question: str
    expected_numeric: float | None = None
    expected_unit: str | None = None
    expected_section: str | None = None


class AblationReport(BaseModel):
    """Aggregated evaluation metrics for one system configuration."""

    mode: str
    total_cases: int
    numerical_accuracy: float
    citation_grounding_rate: float
    abstention_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    failed_case_ids: list[str] = Field(default_factory=list)


def _extract_numbers(text: str) -> list[float]:
    nums: list[float] = []
    for match in _NUM_RE.finditer(text):
        raw = match.group(1).replace(",", "")
        suffix = (match.group(2) or "").lower()
        try:
            val = float(raw)
        except ValueError:
            continue
        if suffix in _MULTIPLIERS:
            val *= _MULTIPLIERS[suffix]
        nums.append(val)
    return nums


def _matches_expected(answer: str, expected: float, rel_tol: float = 0.015) -> bool:
    for candidate in _extract_numbers(answer):
        if expected == 0:
            if candidate == 0:
                return True
            continue
        if abs(candidate - expected) / abs(expected) <= rel_tol:
            return True
    return False


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, math.ceil((pct / 100.0) * len(sorted_vals)) - 1))
    return round(sorted_vals[idx], 2)


def run_ablation_eval(
    mode: str,
    cases: list[EvalCase],
    analyze_fn: Callable[[EvalCase], dict[str, Any]],
) -> AblationReport:
    """Execute `analyze_fn` across `cases` and compute accuracy, grounding, and latency."""
    numeric_total = 0
    numeric_correct = 0
    grounded_count = 0
    abstained_count = 0
    latencies: list[float] = []
    failed_ids: list[str] = []

    for case in cases:
        t0 = time.perf_counter()
        result = analyze_fn(case)
        latencies.append((time.perf_counter() - t0) * 1000.0)

        status = result.get("status", "insufficient_evidence")
        answer = str(result.get("answer", ""))
        citations = result.get("citations", [])

        if status == "insufficient_evidence":
            abstained_count += 1
        elif citations:
            grounded_count += 1

        if case.expected_numeric is not None:
            numeric_total += 1
            if status == "ok" and _matches_expected(answer, case.expected_numeric):
                numeric_correct += 1
            else:
                failed_ids.append(case.case_id)

    latencies.sort()
    n = len(cases) or 1
    return AblationReport(
        mode=mode,
        total_cases=len(cases),
        numerical_accuracy=round((numeric_correct / numeric_total) * 100.0, 2)
        if numeric_total
        else 0.0,
        citation_grounding_rate=round((grounded_count / n) * 100.0, 2),
        abstention_rate=round((abstained_count / n) * 100.0, 2),
        p50_latency_ms=_percentile(latencies, 50),
        p95_latency_ms=_percentile(latencies, 95),
        failed_case_ids=failed_ids,
    )


def render_ablation_markdown(reports: list[AblationReport]) -> str:
    """Format ablation reports as a Markdown comparison table."""
    lines = [
        "| Configuration | Cases | Numerical Accuracy | Citation Grounding | Abstention Rate | p50 (ms) | p95 (ms) |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in reports:
        lines.append(
            f"| {r.mode} | {r.total_cases} | {r.numerical_accuracy:.1f}% | "
            f"{r.citation_grounding_rate:.1f}% | {r.abstention_rate:.1f}% | "
            f"{r.p50_latency_ms:.1f} | {r.p95_latency_ms:.1f} |"
        )
    return "\n".join(lines)
