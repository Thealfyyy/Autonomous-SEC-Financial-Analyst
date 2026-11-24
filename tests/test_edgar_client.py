"""Offline unit tests for Settings and SEC EDGAR client."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from sec_analyst.config import Settings
from sec_analyst.ingestion.edgar_client import EdgarClient


def _build_mock_transport(call_log: list[str]) -> httpx.MockTransport:
    tickers_payload = {
        "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    }
    submissions_payload = {
        "cik": "0001045810",
        "filings": {
            "recent": {
                "form": ["10-Q", "10-K", "10-K/A"],
                "accessionNumber": [
                    "0001045810-24-000010",
                    "0001045810-24-000029",
                    "0001045810-24-000099",
                ],
                "filingDate": ["2024-05-29", "2024-02-21", "2024-03-15"],
                "reportDate": ["2024-04-28", "2024-01-28", "2024-01-28"],
                "primaryDocument": ["nvda-q1.htm", "nvda-20240128.htm", "nvda-20240128a.htm"],
            }
        },
    }
    facts_payload = {"cik": 1045810, "facts": {"us-gaap": {}}}

    def handler(request: httpx.Request) -> httpx.Response:
        call_log.append(f"{request.url} | UA={request.headers.get('user-agent')}")
        url = str(request.url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, json=tickers_payload)
        if url.endswith("CIK0001045810.json") and "submissions" in url:
            return httpx.Response(200, json=submissions_payload)
        if url.endswith("CIK0001045810.json") and "companyfacts" in url:
            return httpx.Response(200, json=facts_payload)
        if url.endswith("nvda-20240128.htm"):
            return httpx.Response(200, text="<html><body>Item 1A. Risk Factors</body></html>")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_settings_enforces_sec_guardrails() -> None:
    with pytest.raises(ValidationError):
        Settings(sec_rate_limit_rps=12.0)

    with pytest.raises(ValidationError):
        Settings(sec_user_agent="BadAgentNoEmail")


def test_edgar_client_fetches_caches_and_filters_10k(tmp_path: Path) -> None:
    calls: list[str] = []
    client = httpx.Client(transport=_build_mock_transport(calls))
    edgar = EdgarClient(
        user_agent="AutonomousSECAnalyst/0.1 test@example.com",
        cache_dir=tmp_path,
        max_rps=10.0,
        http_client=client,
    )

    filings = edgar.get_10k_filings("nvda", fiscal_years={2024})
    assert len(filings) == 2
    assert all(f.form_type in {"10-K", "10-K/A"} for f in filings)
    assert filings[0].cik == "0001045810"
    assert filings[0].filing_url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581024000029/nvda-20240128.htm"
    )
    assert all("UA=AutonomousSECAnalyst/0.1 test@example.com" in c for c in calls)

    # Second call must hit local disk cache with zero new HTTP requests
    before_count = len(calls)
    cached_filings = edgar.get_10k_filings("NVDA", fiscal_years={2024})
    assert len(cached_filings) == 2
    assert len(calls) == before_count

    # Download HTML and verify cached file contents
    html_path = edgar.download_filing_html(filings[0])
    assert html_path.exists()
    assert "Item 1A. Risk Factors" in html_path.read_text(encoding="utf-8")

    # Fetch companyfacts
    facts = edgar.get_company_facts("0001045810")
    assert facts["cik"] == 1045810
    assert json.loads((tmp_path / "0001045810" / "companyfacts.json").read_text())["cik"] == 1045810


def test_edgar_client_paginates_archive_files_for_high_volume_filers(tmp_path: Path) -> None:
    calls: list[str] = []
    tickers_payload = {
        "0": {"cik_str": 19617, "ticker": "JPM", "title": "JPMORGAN CHASE & CO"},
    }
    submissions_payload = {
        "cik": "0000019617",
        "filings": {
            "recent": {
                "form": ["424B2", "424B2"],
                "accessionNumber": ["0000019617-25-000001", "0000019617-25-000002"],
                "filingDate": ["2025-06-01", "2025-06-02"],
                "reportDate": ["2025-06-01", "2025-06-02"],
                "primaryDocument": ["note1.htm", "note2.htm"],
            },
            "files": [
                {
                    "name": "CIK0000019617-submissions-001.json",
                    "filingCount": 1000,
                    "filingFrom": "2024-01-01",
                    "filingTo": "2025-03-01",
                },
                {
                    "name": "CIK0000019617-submissions-old.json",
                    "filingCount": 1000,
                    "filingFrom": "2015-01-01",
                    "filingTo": "2016-12-31",
                },
            ],
        },
    }
    archive_001 = {
        "form": ["424B2", "10-K"],
        "accessionNumber": ["0000019617-25-000100", "0000019617-25-000210"],
        "filingDate": ["2025-02-01", "2025-02-14"],
        "reportDate": ["2025-02-01", "2024-12-31"],
        "primaryDocument": ["note3.htm", "jpm-20241231.htm"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, json=tickers_payload)
        if url.endswith("CIK0000019617.json"):
            return httpx.Response(200, json=submissions_payload)
        if url.endswith("CIK0000019617-submissions-001.json"):
            return httpx.Response(200, json=archive_001)
        return httpx.Response(404)

    edgar = EdgarClient(
        user_agent="AutonomousSECAnalyst/0.1 test@example.com",
        cache_dir=tmp_path,
        max_rps=10.0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    filings = edgar.get_10k_filings("JPM", fiscal_years={2024})
    assert len(filings) == 1
    assert filings[0].accession_number == "0000019617-25-000210"
    assert filings[0].fiscal_year == 2024
    # Old 2015-2016 archive page must be skipped by date-window pruning
    assert not any("submissions-old" in u for u in calls)
    assert (tmp_path / "0000019617" / "CIK0000019617-submissions-001.json").exists()
