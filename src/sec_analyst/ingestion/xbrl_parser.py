"""XBRL companyfacts parser and canonical US-GAAP concept mapper."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from sec_analyst.ingestion.edgar_client import FilingMetadata

# Maps canonical analyst concepts -> ordered US-GAAP XBRL tags (most specific/modern first).
CANONICAL_CONCEPT_MAP: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ),
    "cost_of_revenue": (
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
    ),
    "gross_profit": ("GrossProfit",),
    "research_and_development": ("ResearchAndDevelopmentExpense",),
    "selling_general_admin": ("SellingGeneralAndAdministrativeExpense",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "stockholders_equity": ("StockholdersEquity",),
    "cash_and_equivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
}

# Reverse lookup: raw US-GAAP tag -> canonical concept
RAW_TAG_TO_CANONICAL: dict[str, str] = {
    tag: concept for concept, tags in CANONICAL_CONCEPT_MAP.items() for tag in tags
}


class XbrlFactRecord(BaseModel):
    """Normalized XBRL fact ready for insertion into `xbrl_facts`."""

    accession_number: str
    ticker: str
    fiscal_year: int
    fiscal_period: str = "FY"
    period_end_date: date
    raw_tag: str
    canonical_concept: str | None
    value: Decimal
    unit: str
    decimals: int | None = None
    frame: str | None = None


def _is_full_year_or_instant(entry: dict[str, Any]) -> bool:
    """Keep annual (FY / ~365d) or instant balance-sheet facts; drop partial-year Q1-Q3 noise."""
    fp = entry.get("fp")
    if fp == "FY":
        return True
    # Balance-sheet facts have no `start` date (instantaneous at `end`).
    if "start" not in entry and fp in (None, "FY"):
        return True
    # Fallback: if `start` and `end` span >= 330 days, treat as full fiscal year.
    start_str = entry.get("start")
    end_str = entry.get("end")
    if start_str and end_str:
        days = (date.fromisoformat(end_str) - date.fromisoformat(start_str)).days
        return days >= 330
    return False


def _is_single_quarter_or_instant(entry: dict[str, Any]) -> bool:
    """Keep standalone single-quarter (~70-110d) or balance-sheet instant facts for 10-Q filings."""
    fp = entry.get("fp")
    if fp not in {"Q1", "Q2", "Q3", "Q4"}:
        return False
    if "start" not in entry:
        return True
    start_str = entry.get("start")
    end_str = entry.get("end")
    if start_str and end_str:
        days = (date.fromisoformat(end_str) - date.fromisoformat(start_str)).days
        return 60 <= days <= 115
    return False


def parse_company_facts(
    company_facts: dict[str, Any],
    filings: list[FilingMetadata],
    include_unmapped_tags: bool = False,
) -> list[XbrlFactRecord]:
    """Extract and normalize XBRL facts for the given 10-K / 10-Q filings."""
    filings_by_accn = {f.accession_number: f for f in filings}
    if not filings_by_accn:
        return []

    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    deduped: dict[tuple[str, str, date, str, str], XbrlFactRecord] = {}

    for raw_tag, tag_payload in us_gaap.items():
        canonical = RAW_TAG_TO_CANONICAL.get(raw_tag)
        if canonical is None and not include_unmapped_tags:
            continue

        units_dict = tag_payload.get("units", {})
        for unit_name, entries in units_dict.items():
            for entry in entries:
                accn = entry.get("accn")
                if accn not in filings_by_accn:
                    continue
                filing = filings_by_accn[accn]
                entry_form = str(entry.get("form") or "")
                is_10q_filing = filing.form_type.startswith("10-Q")

                if is_10q_filing:
                    if entry_form not in {"10-Q", "10-Q/A"}:
                        continue
                    if not _is_single_quarter_or_instant(entry):
                        continue
                    fiscal_period = str(entry.get("fp") or filing.fiscal_period)
                else:
                    if entry_form not in {"10-K", "10-K/A"}:
                        continue
                    if not _is_full_year_or_instant(entry):
                        continue
                    fiscal_period = "FY"

                end_str = entry.get("end")
                val = entry.get("val")
                if end_str is None or val is None:
                    continue

                period_end = date.fromisoformat(end_str)
                # Use the filing's fiscal year when the fact's period end matches the filing,
                # otherwise fall back to the period_end calendar year (comparative prior years).
                fy = (
                    filing.fiscal_year
                    if period_end == filing.period_end_date
                    else period_end.year
                )

                key = (accn, raw_tag, period_end, fiscal_period, unit_name)
                deduped[key] = XbrlFactRecord(
                    accession_number=accn,
                    ticker=filing.ticker,
                    fiscal_year=fy,
                    fiscal_period=fiscal_period,
                    period_end_date=period_end,
                    raw_tag=raw_tag,
                    canonical_concept=canonical,
                    value=Decimal(str(val)),
                    unit=unit_name,
                    decimals=entry.get("decimals"),
                    frame=entry.get("frame"),
                )

    # Derive total_liabilities = total_assets - stockholders_equity when a filer (e.g., INTC)
    # omits the standalone US-GAAP `Liabilities` tag.
    by_filing_period: dict[tuple[str, date, str, str], dict[str, XbrlFactRecord]] = {}
    for rec in deduped.values():
        if rec.canonical_concept:
            by_filing_period.setdefault(
                (rec.accession_number, rec.period_end_date, rec.fiscal_period, rec.unit), {}
            )[rec.canonical_concept] = rec

    for (accn, period_end, fiscal_period, unit_name), cmap in by_filing_period.items():
        if "total_liabilities" not in cmap and "total_assets" in cmap and "stockholders_equity" in cmap:
            assets_rec = cmap["total_assets"]
            eq_rec = cmap["stockholders_equity"]
            deduped[
                (accn, "Derived(Assets-StockholdersEquity)", period_end, fiscal_period, unit_name)
            ] = XbrlFactRecord(
                accession_number=accn,
                ticker=assets_rec.ticker,
                fiscal_year=assets_rec.fiscal_year,
                fiscal_period=fiscal_period,
                period_end_date=period_end,
                raw_tag="Derived(Assets-StockholdersEquity)",
                canonical_concept="total_liabilities",
                value=assets_rec.value - eq_rec.value,
                unit=unit_name,
                decimals=assets_rec.decimals,
                frame=assets_rec.frame,
            )

    return sorted(
        deduped.values(),
        key=lambda r: (r.fiscal_year, r.fiscal_period, r.raw_tag, r.period_end_date, r.unit),
    )
