"""4-node LangGraph orchestrator: Planner -> Quant/Qual Workers -> Synthesizer -> Auditor."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import nullcontext
from decimal import Decimal
from typing import Any, Protocol

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from sec_analyst.agents.auditor import verify_answer
from sec_analyst.agents.llm_client import LLMClient
from sec_analyst.agents.state import AnalystState, Citation, QueryPlan
from sec_analyst.observability import TraceRecorder
from sec_analyst.retrieval.hybrid_search import RetrievedContext
from sec_analyst.tools.filing_diff import FilingDiffResult, diff_section_paragraphs
from sec_analyst.tools.financial_math import (
    MetricResult,
    cagr,
    free_cash_flow,
    margin,
    ratio,
    yoy_growth,
)
from sec_analyst.tools.sql_tool import SqlExecutor, SqlResult, SqlValidationError


class RetrieverProtocol(Protocol):
    """Protocol for metadata-filtered hybrid retrieval."""

    def search(
        self,
        query: str,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        item_sections: list[str] | None = None,
        candidate_limit: int = 25,
        top_k_parents: int = 5,
    ) -> list[RetrievedContext]: ...


PLANNER_SYSTEM_PROMPT = """You are a financial research query planner for SEC 10-K and 10-Q filings.
Parse the user question into JSON matching the QueryPlan schema.
Database schema for quantitative_queries (read-only PostgreSQL SELECT only):
- View: v_latest_xbrl_facts(ticker, fiscal_year, fiscal_period, form_type, canonical_concept, raw_tag, value, unit, accession_number, filing_url)
- Always include `ticker, fiscal_year, fiscal_period, canonical_concept, value, unit, accession_number` in SELECT.
- Use `fiscal_period = 'FY'` for annual questions and `fiscal_period IN ('Q1','Q2','Q3')` when the user asks about a specific quarter.
- canonical_concept values: 'revenue', 'cost_of_revenue', 'gross_profit', 'research_and_development', 'selling_general_admin', 'operating_income', 'net_income', 'eps_diluted', 'total_assets', 'total_liabilities', 'stockholders_equity', 'cash_and_equivalents', 'operating_cash_flow', 'capex', 'long_term_debt'
- Whenever the question asks for any financial figure, growth, margin, ratio, % of revenue, or numeric comparison, you MUST populate `quantitative_queries` with at least one SQL SELECT query.
  Example: SELECT ticker, fiscal_year, fiscal_period, canonical_concept, value, unit, accession_number FROM v_latest_xbrl_facts WHERE ticker IN ('AAPL', 'MSFT') AND fiscal_year = 2024 AND fiscal_period = 'FY' AND canonical_concept IN ('revenue', 'research_and_development')
- For qualitative_queries, item_sections MUST be from: ["Item 1", "Item 1A", "Item 1C", "Item 2", "Item 7", "Item 8"] ("Item 2" is 10-Q MD&A).
- Set needs_diff=true ONLY when comparing narrative section changes across >= 2 fiscal years of the same company (e.g. YoY Risk Factor diff).
- Leave math_operations as [] (Python computes YoY growth, margins, ratios, and FCF automatically from SQL rows).
- Set abstain=false unless the ticker or fiscal year is missing from Indexed tickers / Indexed fiscal years."""

SYNTHESIZER_SYSTEM_PROMPT = """You are a strict SEC financial analyst synthesizer.
Write a concise, accurate financial answer using ONLY the provided SQL results, deterministic Python metrics, and SEC filing excerpts.
Never perform mental arithmetic; quote only numbers present in sql_results or computed_metrics.
When quoting USD amounts from sql_results, copy the exact pre-formatted scale in `formatted_value` (e.g. "$245.12 billion") — never rescale or count zeros manually.
Output JSON matching {"answer": "...", "citations": [{"ticker": "...", "fiscal_year": 2024, "item_section": "Item 1A", "accession_number": "...", "filing_url": "..."}]}."""


class _SynthesizerOutput(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)


def _format_sql_row_for_llm(row: dict[str, Any]) -> dict[str, Any]:
    """Attach a deterministic `formatted_value` so small LLMs never mis-count zeros or units."""
    enriched = dict(row)
    val = row.get("value")
    unit = str(row.get("unit") or "").strip()
    if val is None or isinstance(val, bool):
        return enriched
    try:
        dec = Decimal(str(val))
    except Exception:  # noqa: BLE001
        return enriched

    unit_lower = unit.lower()
    abs_val = abs(dec)
    sign = "-" if dec < 0 else ""
    if unit_lower == "usd":
        if abs_val >= Decimal(1000000000000):
            scaled = (abs_val / Decimal(1000000000000)).quantize(Decimal("0.01"))
            enriched["formatted_value"] = f"{sign}${scaled} trillion ({sign}${abs_val:,.0f})"
        elif abs_val >= Decimal(1000000000):
            scaled = (abs_val / Decimal(1000000000)).quantize(Decimal("0.01"))
            enriched["formatted_value"] = f"{sign}${scaled} billion ({sign}${abs_val:,.0f})"
        elif abs_val >= Decimal(1000000):
            scaled = (abs_val / Decimal(1000000)).quantize(Decimal("0.01"))
            enriched["formatted_value"] = f"{sign}${scaled} million ({sign}${abs_val:,.0f})"
        else:
            enriched["formatted_value"] = f"{sign}${abs_val:,.2f}"
    elif "share" in unit_lower:
        enriched["formatted_value"] = f"{sign}${abs_val:.2f} per share"
    else:
        enriched["formatted_value"] = f"{dec:,}"
    return enriched


def _run_math_op(op_name: str, metric_name: str, args: dict[str, Any]) -> MetricResult:
    if op_name == "yoy_growth":
        return yoy_growth(args["current"], args["previous"], metric_name=metric_name)
    if op_name == "margin":
        return margin(args["numerator"], args["denominator"], metric_name=metric_name)
    if op_name == "cagr":
        return cagr(args["start_value"], args["end_value"], int(args["periods"]), metric_name=metric_name)
    if op_name == "ratio":
        return ratio(args["numerator"], args["denominator"], metric_name=metric_name)
    if op_name == "free_cash_flow":
        return free_cash_flow(args["operating_cash_flow"], args["capex"])
    raise ValueError(f"Unsupported math operation: {op_name}")


def _auto_compute_metrics(sql_results: list[SqlResult], computed_metrics: list[MetricResult]) -> None:
    """Deterministically compute YoY growth, margins, ratios, and FCF from SQL rows."""
    series: dict[tuple[str, str, str], dict[int, Decimal]] = {}
    by_ticker_year: dict[tuple[str, int], dict[str, Decimal]] = {}

    for res in sql_results:
        for row in res.rows:
            t = row.get("ticker")
            fy = row.get("fiscal_year")
            val = row.get("value")
            concept = row.get("canonical_concept") or row.get("raw_tag") or "metric"
            unit = row.get("unit") or "USD"
            if t and isinstance(fy, int) and val is not None:
                dec_val = Decimal(str(val))
                ticker_str = str(t).upper()
                concept_str = str(concept)
                series.setdefault((ticker_str, concept_str, str(unit)), {})[fy] = dec_val
                by_ticker_year.setdefault((ticker_str, fy), {})[concept_str] = dec_val

    existing_names = {m.name for m in computed_metrics}
    for (t, concept, _unit), by_year in series.items():
        years_sorted = sorted(by_year.keys())
        for i in range(1, len(years_sorted)):
            y_prev, y_curr = years_sorted[i - 1], years_sorted[i]
            if by_year[y_prev] != 0:
                m_name = f"{t}_{concept}_yoy_{y_prev}_{y_curr}"
                if m_name not in existing_names:
                    computed_metrics.append(
                        yoy_growth(by_year[y_curr], by_year[y_prev], metric_name=m_name)
                    )
                    existing_names.add(m_name)

    margin_specs = [
        ("gross_profit", "revenue", "gross_margin_pct", margin),
        ("operating_income", "revenue", "operating_margin_pct", margin),
        ("net_income", "revenue", "net_margin_pct", margin),
        ("research_and_development", "revenue", "rd_pct_of_revenue", margin),
        ("selling_general_admin", "revenue", "sga_pct_of_revenue", margin),
        ("total_liabilities", "stockholders_equity", "liabilities_to_equity", ratio),
        ("long_term_debt", "stockholders_equity", "long_term_debt_to_equity", ratio),
    ]
    for (t, fy), concepts in sorted(by_ticker_year.items()):
        for num_key, den_key, label, fn in margin_specs:
            if num_key in concepts and den_key in concepts and concepts[den_key] != 0:
                m_name = f"{t}_FY{fy}_{label}"
                if m_name not in existing_names:
                    computed_metrics.append(
                        fn(concepts[num_key], concepts[den_key], metric_name=m_name)
                    )
                    existing_names.add(m_name)
        if "operating_cash_flow" in concepts and "capex" in concepts:
            m_name = f"{t}_FY{fy}_free_cash_flow"
            if m_name not in existing_names:
                fcf = free_cash_flow(concepts["operating_cash_flow"], concepts["capex"])
                computed_metrics.append(fcf.model_copy(update={"name": m_name}))
                existing_names.add(m_name)


def build_analyst_graph(
    llm: LLMClient,
    sql_executor: SqlExecutor,
    retriever: RetrieverProtocol,
    diff_loader: Callable[[str, str, int], list[str]] | None = None,
    trace: TraceRecorder | None = None,
):
    """Compile and return the stateful 4-node LangGraph workflow."""

    def _span(name: str, **attrs: Any):
        return trace.span(name, **attrs) if trace is not None else nullcontext()

    def planner_node(state: AnalystState) -> AnalystState:
        indexed_tickers = {t.upper() for t in state.get("indexed_tickers", [])}
        indexed_years = set(state.get("indexed_years", []))
        indexed_periods = {p.upper() for p in state.get("indexed_periods", ["FY"])}
        has_quarterly = bool(indexed_periods & {"Q1", "Q2", "Q3"})

        with _span("planner", question=state["question"]):
            question = state["question"]
            unsupported_scope = re.search(
                r"\b(8-K|intraday|stock price|market cap|p/e|price target|forecast|projected|2027|2028|bitcoin)\b",
                question,
                re.IGNORECASE,
            )
            quarterly_scope = (
                None
                if has_quarterly
                else re.search(r"\b(10-Q|Q[1-4]|quarterly)\b", question, re.IGNORECASE)
            )
            blocked_match = unsupported_scope or quarterly_scope
            if blocked_match:
                reason_detail = (
                    "no quarterly 10-Q filings are currently indexed."
                    if quarterly_scope and not unsupported_scope
                    else "only indexed SEC 10-K/10-Q filings are supported."
                )
                abstain_plan = QueryPlan(
                    abstain=True,
                    abstain_reason=f"Unsupported scope ('{blocked_match.group(0)}'): {reason_detail}",
                )
                events = list(state.get("events", []))
                events.append({"node": "planner", "plan": abstain_plan.model_dump()})
                return {
                    "plan": abstain_plan,
                    "retry_count": 0,
                    "status": "insufficient_evidence",
                    "abstain_reason": abstain_plan.abstain_reason,
                    "events": events,
                }

            prompt = (
                f"Indexed tickers: {sorted(indexed_tickers)}\n"
                f"Indexed fiscal years: {sorted(indexed_years)}\n"
                f"Indexed fiscal periods: {sorted(indexed_periods)}\n"
                f"Question: {question}"
            )
            raw_plan = llm.chat_json(
                PLANNER_SYSTEM_PROMPT,
                prompt,
                json_schema=QueryPlan.model_json_schema(),
            )
            # Coerce any stringified/malformed math_operations or invalid item_sections cleanly
            if isinstance(raw_plan.get("math_operations"), list):
                raw_plan["math_operations"] = [
                    op for op in raw_plan["math_operations"] if isinstance(op, dict)
                ]
            valid_items = {"Item 1", "Item 1A", "Item 1C", "Item 2", "Item 7", "Item 8"}
            if isinstance(raw_plan.get("qualitative_queries"), list):
                for qt in raw_plan["qualitative_queries"]:
                    if isinstance(qt, dict) and isinstance(qt.get("item_sections"), list):
                        cleaned_items = [s for s in qt["item_sections"] if s in valid_items]
                        qt["item_sections"] = cleaned_items or ["Item 1A", "Item 7"]
            plan = QueryPlan.model_validate(raw_plan)
            plan.tickers = [t.upper() for t in plan.tickers]

            # Deterministic extraction of explicit years (including FY2025) & uppercase ticker symbols
            explicit_years = {int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", question)}
            if explicit_years:
                plan.fiscal_years = sorted(set(plan.fiscal_years) | explicit_years)

            non_ticker_tokens = {
                "FY", "CY", "USD", "SEC", "GAAP", "AI", "EV", "FSD", "TAC", "SQL", "API",
                "RAG", "LLM", "CEO", "CFO", "GPU", "CPU", "ML", "US", "UK", "EU", "CAGR",
                "EPS", "FCF", "YOY", "EBITDA", "ITEM", "CISO", "TSMC", "NBA", "IT", "OR",
                "AND", "IN", "ON", "OF", "TO", "VS", "AS", "BY", "FOR", "THE", "MD", "SG",
                "RD", "CO", "INC", "CORP", "LTD", "LLC", "USA",
            }
            for tok in re.findall(r"\b[A-Z]{2,5}\b", question):
                if tok not in non_ticker_tokens and tok not in plan.tickers:
                    plan.tickers.append(tok)

            if re.search(r"\b(change|changed|diff|differ|evolution)\b", question, re.IGNORECASE) and len(set(plan.fiscal_years)) >= 2:
                plan.needs_diff = True
                if re.search(r"\b(Item\s*7|MD&A)\b", question, re.IGNORECASE):
                    plan.diff_section = "Item 7"
                elif re.search(r"\bItem\s*1C\b", question, re.IGNORECASE):
                    plan.diff_section = "Item 1C"
                else:
                    plan.diff_section = "Item 1A"

            # Enforce deterministic negative boundaries on unindexed tickers / fiscal years.
            missing_tickers = [t for t in plan.tickers if indexed_tickers and t not in indexed_tickers]
            missing_years = [y for y in plan.fiscal_years if indexed_years and y not in indexed_years]
            has_diff_task = plan.needs_diff and len(set(plan.fiscal_years)) >= 2
            no_tasks = not plan.quantitative_queries and not plan.qualitative_queries and not has_diff_task
            if missing_tickers or missing_years:
                plan.abstain = True
                plan.abstain_reason = (
                    f"Requested data outside indexed corpus "
                    f"(unindexed tickers={missing_tickers}, unindexed years={missing_years})."
                )
            elif no_tasks:
                plan.abstain = True
                plan.abstain_reason = plan.abstain_reason or "No valid query tasks generated."
            else:
                plan.abstain = False
                plan.abstain_reason = ""

            events = list(state.get("events", []))
            events.append({"node": "planner", "plan": plan.model_dump()})
            return {
                "plan": plan,
                "retry_count": 0,
                "status": "insufficient_evidence" if plan.abstain else "ok",
                "abstain_reason": plan.abstain_reason,
                "events": events,
            }

    def workers_node(state: AnalystState) -> AnalystState:
        plan = state.get("plan")
        if plan is None:
            raise ValueError("QueryPlan missing from state in workers_node.")

        with _span("workers", tickers=plan.tickers, fiscal_years=plan.fiscal_years):
            events = list(state.get("events", []))
            sql_results: list[SqlResult] = []
            for sql in plan.quantitative_queries:
                try:
                    sql_results.append(sql_executor.execute(sql))
                except SqlValidationError as exc:
                    events.append({"node": "workers", "sql_blocked": str(exc)})

            computed_metrics: list[MetricResult] = []
            for math_op in plan.math_operations:
                computed_metrics.append(_run_math_op(math_op.op, math_op.metric_name, math_op.args))

            # Auto-fetch missing denominator (`revenue`, `capex`, `stockholders_equity`) when
            # the Planner queried only the numerator for a margin/ratio/FCF question.
            seen_concepts: set[tuple[str, int, str]] = set()
            need_revenue_tickers: set[str] = set()
            need_revenue_years: set[int] = set()
            for res in sql_results:
                for row in res.rows:
                    t = row.get("ticker")
                    fy = row.get("fiscal_year")
                    c = row.get("canonical_concept")
                    if t and isinstance(fy, int) and c:
                        seen_concepts.add((str(t).upper(), fy, str(c)))
            for t, fy, c in list(seen_concepts):
                if (
                    c in {"gross_profit", "operating_income", "net_income", "research_and_development", "selling_general_admin"}
                    and (t, fy, "revenue") not in seen_concepts
                ):
                    need_revenue_tickers.add(t)
                    need_revenue_years.add(fy)
            if need_revenue_tickers and need_revenue_years:
                t_list = ", ".join(f"'{t}'" for t in sorted(need_revenue_tickers))
                y_list = ", ".join(str(y) for y in sorted(need_revenue_years))
                denom_sql = (
                    "SELECT ticker, fiscal_year, canonical_concept, value, unit, accession_number "
                    f"FROM v_latest_xbrl_facts WHERE ticker IN ({t_list}) "
                    f"AND fiscal_year IN ({y_list}) AND canonical_concept = 'revenue'"
                )
                try:
                    sql_results.append(sql_executor.execute(denom_sql))
                except SqlValidationError as exc:
                    events.append({"node": "workers", "sql_blocked": str(exc)})

            _auto_compute_metrics(sql_results, computed_metrics)

            retrieved_contexts: list[RetrievedContext] = []
            seen_parents: set[int] = set()
            for q_task in plan.qualitative_queries:
                hits = retriever.search(
                    query=q_task.query,
                    tickers=plan.tickers or None,
                    fiscal_years=plan.fiscal_years or None,
                    item_sections=q_task.item_sections or None,
                )
                for hit in hits:
                    if hit.parent_id not in seen_parents:
                        seen_parents.add(hit.parent_id)
                        retrieved_contexts.append(hit)

            # Attach up to 2 supporting Item 7 / Item 8 excerpts on pure-quantitative queries
            # so the 10-K Excerpts drawer always provides filing provenance context.
            if not retrieved_contexts and any(r.row_count > 0 for r in sql_results):
                hits = retriever.search(
                    query=state["question"],
                    tickers=plan.tickers[:3] or None,
                    fiscal_years=plan.fiscal_years[-2:] or None,
                    item_sections=["Item 7", "Item 8"],
                    top_k_parents=2,
                )
                for hit in hits:
                    if hit.parent_id not in seen_parents:
                        seen_parents.add(hit.parent_id)
                        retrieved_contexts.append(hit)

            diff_results: list[FilingDiffResult] = []
            if plan.needs_diff and diff_loader is not None and len(plan.fiscal_years) >= 2:
                years_sorted = sorted(set(plan.fiscal_years))
                for t in plan.tickers:
                    for i in range(1, len(years_sorted)):
                        y_prev, y_curr = years_sorted[i - 1], years_sorted[i]
                        prev_paras = diff_loader(t, plan.diff_section, y_prev)
                        curr_paras = diff_loader(t, plan.diff_section, y_curr)
                        if prev_paras and curr_paras:
                            diff_results.append(
                                diff_section_paragraphs(
                                    ticker=t,
                                    item_section=plan.diff_section,
                                    previous_year=y_prev,
                                    previous_paragraphs=prev_paras,
                                    current_year=y_curr,
                                    current_paragraphs=curr_paras,
                                )
                            )

            has_evidence = (
                any(r.row_count > 0 for r in sql_results)
                or bool(retrieved_contexts)
                or bool(diff_results)
            )
            events.append(
                {
                    "node": "workers",
                    "sql_queries": len(sql_results),
                    "computed_metrics": len(computed_metrics),
                    "retrieved_parents": len(retrieved_contexts),
                    "diff_results": len(diff_results),
                }
            )
            return {
                "sql_results": sql_results,
                "computed_metrics": computed_metrics,
                "retrieved_contexts": retrieved_contexts,
                "diff_results": diff_results,
                "status": "ok" if has_evidence else "insufficient_evidence",
                "abstain_reason": "" if has_evidence else "No matching records found in indexed SEC filings.",
                "events": events,
            }

    def synthesizer_node(state: AnalystState) -> AnalystState:
        sql_results = state.get("sql_results", [])
        computed_metrics = state.get("computed_metrics", [])
        retrieved_contexts = state.get("retrieved_contexts", [])
        diff_results = state.get("diff_results", [])
        prior_audit = state.get("audit_result")

        with _span("synthesizer", retry=state.get("retry_count", 0)):
            compact_contexts = [
                {
                    **c.model_dump(mode="json"),
                    "content": c.content[:2500],
                }
                for c in retrieved_contexts[:5]
            ]
            compact_diffs = [
                {
                    "ticker": d.ticker,
                    "item_section": d.item_section,
                    "previous_year": d.previous_year,
                    "current_year": d.current_year,
                    "unchanged_count": d.unchanged_count,
                    "added_paragraphs": [p[:800] for p in d.added_paragraphs[:3]],
                    "removed_paragraphs": [p[:800] for p in d.removed_paragraphs[:3]],
                    "modified_count": len(d.modified_paragraphs),
                }
                for d in diff_results[:3]
            ]
            evidence_payload = {
                "question": state["question"],
                "sql_results": [
                    {
                        **r.model_dump(mode="json"),
                        "rows": [_format_sql_row_for_llm(row) for row in r.rows[:25]],
                    }
                    for r in sql_results
                ],
                "computed_metrics": [m.model_dump(mode="json") for m in computed_metrics],
                "retrieved_contexts": compact_contexts,
                "diff_results": compact_diffs,
                "audit_feedback": prior_audit.issues if (prior_audit and not prior_audit.passed) else [],
            }
            raw = llm.chat_json(
                SYNTHESIZER_SYSTEM_PROMPT,
                json.dumps(evidence_payload, default=str),
                json_schema=_SynthesizerOutput.model_json_schema(),
            )
            answer = str(raw.get("answer", ""))
            citations = [Citation.model_validate(c) for c in raw.get("citations", [])]

            # Deterministically attach grounded citations if the LLM omitted them.
            if not citations:
                seen_cites: set[tuple[str, int, str, str]] = set()
                for ctx in retrieved_contexts[:3]:
                    key = (ctx.ticker, ctx.fiscal_year, ctx.item_section, ctx.accession_number)
                    if key not in seen_cites:
                        seen_cites.add(key)
                        citations.append(
                            Citation(
                                ticker=ctx.ticker,
                                fiscal_year=ctx.fiscal_year,
                                item_section=ctx.item_section,
                                accession_number=ctx.accession_number,
                                filing_url=ctx.filing_url,
                            )
                        )
                if not citations:
                    for res in sql_results:
                        for row in res.rows:
                            t = row.get("ticker")
                            fy = row.get("fiscal_year")
                            accn = row.get("accession_number")
                            if t and isinstance(fy, int) and accn:
                                key = (str(t), fy, "Item 8", str(accn))
                                if key not in seen_cites:
                                    seen_cites.add(key)
                                    citations.append(
                                        Citation(
                                            ticker=str(t),
                                            fiscal_year=fy,
                                            item_section="Item 8",
                                            accession_number=str(accn),
                                            filing_url=str(
                                                row.get("filing_url")
                                                or f"https://www.sec.gov/Archives/edgar/data/{accn}"
                                            ),
                                        )
                                    )

            events = list(state.get("events", []))
            events.append({"node": "synthesizer", "citation_count": len(citations)})
            out: AnalystState = {
                "draft_answer": answer,
                "citations": citations,
                "events": events,
            }
            if (
                not sql_results
                and not computed_metrics
                and re.match(
                    r"^\s*(?:The provided|Insufficient|There is no|I cannot)",
                    answer,
                    re.IGNORECASE,
                )
                and re.search(
                    r"\b(does not include|does not disclose|not disclosed|no information|cannot be determined|not available)\b",
                    answer,
                    re.IGNORECASE,
                )
            ):
                out["status"] = "insufficient_evidence"
                out["abstain_reason"] = answer
            return out

    def auditor_node(state: AnalystState) -> AnalystState:
        with _span("auditor", retry=state.get("retry_count", 0)):
            audit = verify_answer(
                draft_answer=state.get("draft_answer", ""),
                citations=state.get("citations", []),
                sql_results=state.get("sql_results", []),
                computed_metrics=state.get("computed_metrics", []),
                retrieved_contexts=state.get("retrieved_contexts", []),
                indexed_years=state.get("indexed_years", []),
                diff_results=state.get("diff_results", []),
            )
            retry_count = state.get("retry_count", 0)
            events = list(state.get("events", []))
            events.append({"node": "auditor", "passed": audit.passed, "issues": audit.issues})

            if audit.passed:
                return {"audit_result": audit, "status": "ok", "events": events}

            if retry_count < 1:
                return {
                    "audit_result": audit,
                    "retry_count": retry_count + 1,
                    "events": events,
                }

            return {
                "audit_result": audit,
                "status": "insufficient_evidence",
                "abstain_reason": "Auditor verification failed after retry: " + "; ".join(audit.issues),
                "events": events,
            }

    def abstain_node(state: AnalystState) -> AnalystState:
        reason = state.get("abstain_reason") or "Insufficient verified evidence in indexed SEC filings."
        events = list(state.get("events", []))
        events.append({"node": "abstain", "reason": reason})
        return {
            "status": "insufficient_evidence",
            "draft_answer": f"Insufficient evidence: {reason}",
            "citations": [],
            "events": events,
        }

    def route_after_planner(state: AnalystState) -> str:
        return "abstain" if state.get("status") == "insufficient_evidence" else "workers"

    def route_after_workers(state: AnalystState) -> str:
        return "abstain" if state.get("status") == "insufficient_evidence" else "synthesizer"

    def route_after_synthesizer(state: AnalystState) -> str:
        return "abstain" if state.get("status") == "insufficient_evidence" else "auditor"

    def route_after_auditor(state: AnalystState) -> str:
        audit = state.get("audit_result")
        if audit and audit.passed:
            return END
        if state.get("status") == "insufficient_evidence":
            return "abstain"
        return "synthesizer"

    graph = StateGraph(AnalystState)
    graph.add_node("planner", planner_node)
    graph.add_node("workers", workers_node)
    graph.add_node("synthesizer", synthesizer_node)
    graph.add_node("auditor", auditor_node)
    graph.add_node("abstain", abstain_node)

    graph.set_entry_point("planner")
    graph.add_conditional_edges("planner", route_after_planner, {"abstain": "abstain", "workers": "workers"})
    graph.add_conditional_edges("workers", route_after_workers, {"abstain": "abstain", "synthesizer": "synthesizer"})
    graph.add_conditional_edges(
        "synthesizer",
        route_after_synthesizer,
        {"abstain": "abstain", "auditor": "auditor"},
    )
    graph.add_conditional_edges(
        "auditor",
        route_after_auditor,
        {"synthesizer": "synthesizer", "abstain": "abstain", END: END},
    )
    graph.add_edge("abstain", END)

    return graph.compile()
