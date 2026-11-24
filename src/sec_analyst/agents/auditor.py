"""Deterministic Auditor node verifying every number and citation against tool outputs."""

from __future__ import annotations

import re
from contextlib import suppress
from decimal import Decimal, InvalidOperation

from sec_analyst.agents.state import AuditResult, Citation
from sec_analyst.retrieval.hybrid_search import RetrievedContext
from sec_analyst.tools.filing_diff import FilingDiffResult
from sec_analyst.tools.financial_math import MetricResult
from sec_analyst.tools.sql_tool import SqlResult

# Matches numbers like $60.92B, 60,922,000,000, +125.85%, 72.72%, 1.50x
_NUMBER_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])([+\-]?\s*\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(%|billion|million|thousand|B|M|K|x)?\b",
    re.IGNORECASE,
)

_SCALE_MULTIPLIERS: dict[str, Decimal] = {
    "billion": Decimal(1000000000),
    "b": Decimal(1000000000),
    "million": Decimal(1000000),
    "m": Decimal(1000000),
    "thousand": Decimal(1000),
    "k": Decimal(1000),
}

# Numbers that represent calendar years, SEC form names, Item/ASC/Section/Context numbers, or COVID-19.
_IGNORE_ITEM_NUM_RE = re.compile(
    r"\b(?:Item|Form|10-K|10-Q|8-K|FY|CY|COVID|ASC|Section|Windows|Context)\s*[:\-]?\s*\d+",
    re.IGNORECASE,
)


def _extract_text_numbers(text: str, verified: list[Decimal]) -> None:
    cleaned_ctx = _IGNORE_ITEM_NUM_RE.sub(" ", text)
    for match in _NUMBER_TOKEN_RE.finditer(cleaned_ctx):
        raw_num = match.group(1).replace(",", "").replace("$", "").replace(" ", "")
        suffix = (match.group(2) or "").lower()
        if not raw_num or raw_num in {"+", "-"}:
            continue
        with suppress(InvalidOperation):
            val = Decimal(raw_num)
            verified.append(
                val * _SCALE_MULTIPLIERS[suffix] if suffix in _SCALE_MULTIPLIERS else val
            )


def _collect_verified_numbers(
    sql_results: list[SqlResult],
    computed_metrics: list[MetricResult],
    retrieved_contexts: list[RetrievedContext] | None = None,
    diff_results: list[FilingDiffResult] | None = None,
) -> list[Decimal]:
    """Gather ground-truth numeric values from SQL rows, Python math, retrieved 10-K text, and diffs."""
    verified: list[Decimal] = []

    for res in sql_results:
        for row in res.rows:
            for col_name, val in row.items():
                if col_name.lower() in {"fiscal_year", "id", "decimals"}:
                    continue
                if isinstance(val, (int, float, Decimal)):
                    verified.append(Decimal(str(val)))

    for metric in computed_metrics:
        verified.append(metric.value)
        for inp_val in metric.inputs.values():
            with suppress(InvalidOperation):
                verified.append(Decimal(inp_val))

    if retrieved_contexts:
        for ctx in retrieved_contexts:
            _extract_text_numbers(ctx.content, verified)

    if diff_results:
        for diff in diff_results:
            for para in diff.added_paragraphs:
                _extract_text_numbers(para, verified)
            for para in diff.removed_paragraphs:
                _extract_text_numbers(para, verified)
            for mod in diff.modified_paragraphs:
                _extract_text_numbers(mod.previous_text, verified)
                _extract_text_numbers(mod.current_text, verified)

    return verified


def _matches_verified(candidate: Decimal, verified: list[Decimal], rel_tol: float = 0.01) -> bool:
    """Return True if candidate matches any verified value within rounding tolerance."""
    for target in verified:
        if target == 0:
            if candidate == 0:
                return True
            continue
        diff_ratio = abs(float(candidate - target)) / abs(float(target))
        if diff_ratio <= rel_tol:
            return True
    return False


def verify_answer(
    draft_answer: str,
    citations: list[Citation],
    sql_results: list[SqlResult],
    computed_metrics: list[MetricResult],
    retrieved_contexts: list[RetrievedContext],
    indexed_years: list[int],
    diff_results: list[FilingDiffResult] | None = None,
) -> AuditResult:
    """Deterministically verify numbers and citations in `draft_answer`."""
    issues: list[str] = []
    unverified_numbers: list[str] = []
    missing_citations: list[str] = []

    verified_nums = _collect_verified_numbers(
        sql_results, computed_metrics, retrieved_contexts, diff_results
    )
    year_set = {Decimal(y) for y in indexed_years} | {Decimal(y) for y in range(2000, 2035)}

    # Strip SEC form/item labels, markdown/inline numbered-list prefixes ("1. ", "(1) "), and bracketed citation indices ("[1]").
    sanitized_text = _IGNORE_ITEM_NUM_RE.sub(" ", draft_answer)
    sanitized_text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*•]\s+|\*{1,2})?\d+[.)]\s*", "", sanitized_text)
    sanitized_text = re.sub(r"\[\d{1,2}\]|(?<!\$)\(\d{1,2}\)(?=\s)", " ", sanitized_text)

    for match in _NUMBER_TOKEN_RE.finditer(sanitized_text):
        raw_num = match.group(1).replace(",", "").replace("$", "").replace(" ", "")
        suffix = (match.group(2) or "").lower()
        if not raw_num or raw_num in {"+", "-"}:
            continue

        try:
            val = Decimal(raw_num)
        except InvalidOperation:
            continue

        # Ignore standalone calendar years when no scale/percent suffix is attached.
        if not suffix and val in year_set:
            continue

        scaled_val = val * _SCALE_MULTIPLIERS[suffix] if suffix in _SCALE_MULTIPLIERS else val
        if not verified_nums:
            unverified_numbers.append(match.group(0).strip())
            continue

        if not (
            _matches_verified(scaled_val, verified_nums)
            or _matches_verified(val, verified_nums)
        ):
            unverified_numbers.append(match.group(0).strip())

    if unverified_numbers:
        issues.append(
            "Unverified numeric values found in draft: " + ", ".join(unverified_numbers)
        )

    # Verify every citation maps to an actually retrieved context or SQL accession/filing.
    valid_accns = {ctx.accession_number for ctx in retrieved_contexts}
    for res in sql_results:
        for row in res.rows:
            if "accession_number" in row:
                valid_accns.add(str(row["accession_number"]))

    for cite in citations:
        if cite.accession_number not in valid_accns:
            missing_citations.append(
                f"{cite.ticker} FY{cite.fiscal_year} {cite.item_section} ({cite.accession_number})"
            )

    if retrieved_contexts and not citations:
        issues.append("Qualitative evidence was retrieved, but no citations were attached.")
    if missing_citations:
        issues.append("Citations not grounded in retrieved evidence: " + ", ".join(missing_citations))

    return AuditResult(
        passed=(len(issues) == 0),
        issues=issues,
        unverified_numbers=unverified_numbers,
        missing_citations=missing_citations,
    )
