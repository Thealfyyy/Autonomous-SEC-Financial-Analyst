"""Offline unit tests for the XBRL companyfacts parser & canonical mapper."""

from datetime import date
from decimal import Decimal

from sec_analyst.ingestion.edgar_client import FilingMetadata
from sec_analyst.ingestion.xbrl_parser import parse_company_facts


def test_parse_company_facts_maps_canonical_and_filters_quarterly() -> None:
    filing = FilingMetadata(
        accession_number="0001045810-24-000029",
        cik="0001045810",
        ticker="NVDA",
        company_name="NVIDIA CORP",
        form_type="10-K",
        fiscal_year=2024,
        filing_date=date(2024, 2, 21),
        period_end_date=date(2024, 1, 28),
        primary_document="nvda-20240128.htm",
        filing_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581024000029/nvda-20240128.htm",
    )

    company_facts = {
        "cik": 1045810,
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "accn": "0001045810-24-000029",
                                "form": "10-K",
                                "fp": "FY",
                                "start": "2023-01-30",
                                "end": "2024-01-28",
                                "val": 60922000000,
                                "decimals": -6,
                                "frame": "CY2023",
                            },
                            # Quarterly noise inside the same filing should be dropped
                            {
                                "accn": "0001045810-24-000029",
                                "form": "10-K",
                                "fp": "Q4",
                                "start": "2023-10-30",
                                "end": "2024-01-28",
                                "val": 22103000000,
                            },
                            # Unrelated accession number should be ignored
                            {
                                "accn": "9999999999-24-000001",
                                "form": "10-K",
                                "fp": "FY",
                                "start": "2023-01-30",
                                "end": "2024-01-28",
                                "val": 123,
                            },
                        ]
                    }
                },
                "SomeObscureCustomTag": {
                    "units": {
                        "USD": [
                            {
                                "accn": "0001045810-24-000029",
                                "form": "10-K",
                                "fp": "FY",
                                "end": "2024-01-28",
                                "val": 999,
                            }
                        ]
                    }
                },
            }
        },
    }

    records = parse_company_facts(company_facts, [filing])
    assert len(records) == 1
    rec = records[0]
    assert rec.canonical_concept == "revenue"
    assert rec.value == Decimal(60922000000)
    assert rec.fiscal_year == 2024
    assert rec.unit == "USD"


def test_parse_company_facts_extracts_single_quarter_10q_and_skips_ytd() -> None:
    filing_10q = FilingMetadata(
        accession_number="0001045810-24-000120",
        cik="0001045810",
        ticker="NVDA",
        company_name="NVIDIA CORP",
        form_type="10-Q",
        fiscal_year=2024,
        fiscal_period="Q2",
        filing_date=date(2024, 8, 28),
        period_end_date=date(2024, 7, 28),
        primary_document="nvda-20240728.htm",
        filing_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581024000120/nvda-20240728.htm",
    )
    company_facts = {
        "cik": 1045810,
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            # Standalone 3-month Q2 revenue (kept)
                            {
                                "accn": "0001045810-24-000120",
                                "form": "10-Q",
                                "fp": "Q2",
                                "start": "2024-04-29",
                                "end": "2024-07-28",
                                "val": 30040000000,
                            },
                            # 6-month YTD revenue (skipped so it doesn't overwrite Q2)
                            {
                                "accn": "0001045810-24-000120",
                                "form": "10-Q",
                                "fp": "Q2",
                                "start": "2024-01-29",
                                "end": "2024-07-28",
                                "val": 56084000000,
                            },
                        ]
                    }
                }
            }
        },
    }
    records = parse_company_facts(company_facts, [filing_10q])
    assert len(records) == 1
    assert records[0].fiscal_period == "Q2"
    assert records[0].value == Decimal(30040000000)
