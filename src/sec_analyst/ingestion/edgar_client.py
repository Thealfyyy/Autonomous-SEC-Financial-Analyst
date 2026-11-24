"""Compliant SEC EDGAR downloader with rate limiting and local disk caching."""

from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Self

import httpx
from pydantic import BaseModel

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_SUBMISSIONS_ARCHIVE_URL = "https://data.sec.gov/submissions/{filename}"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_ARCHIVES_DOC_URL = (
    "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{primary_doc}"
)


class FilingMetadata(BaseModel):
    """Metadata for a single SEC 10-K / 10-Q filing (including /A amendments)."""

    accession_number: str
    cik: str
    ticker: str
    company_name: str
    form_type: str
    fiscal_year: int
    fiscal_period: str = "FY"
    filing_date: date
    period_end_date: date
    primary_document: str
    filing_url: str
    is_amendment: bool = False


class RateLimiter:
    """Thread-safe interval rate limiter keeping requests <= max_rps."""

    def __init__(self, max_rps: float = 8.0) -> None:
        if max_rps <= 0 or max_rps > 10.0:
            raise ValueError("max_rps must be in (0, 10.0] to respect SEC fair-access policy.")
        self._min_interval = 1.0 / max_rps
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self) -> None:
        """Block until the next request slot is permitted."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call = time.monotonic()


class EdgarClient:
    """Downloads and caches SEC EDGAR filings and XBRL JSON data."""

    def __init__(
        self,
        user_agent: str,
        cache_dir: Path | str = Path("data/raw_sec"),
        max_rps: float = 8.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("A descriptive User-Agent header is required by SEC EDGAR.")
        self.user_agent = user_agent.strip()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._limiter = RateLimiter(max_rps=max_rps)
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=30.0,
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get_cached_bytes(self, url: str, cache_path: Path, force: bool = False) -> bytes:
        """Return cached bytes if present, otherwise fetch via rate-limited HTTP GET."""
        if cache_path.exists() and not force:
            return cache_path.read_bytes()

        self._limiter.wait()
        response = self._client.get(url, headers={"User-Agent": self.user_agent})
        response.raise_for_status()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)
        return response.content

    def get_cik_for_ticker(self, ticker: str) -> tuple[str, str]:
        """Resolve a stock ticker (e.g., 'NVDA') to (zero-padded 10-digit CIK, company_name)."""
        target = ticker.strip().upper()
        cache_path = self.cache_dir / "company_tickers.json"
        raw = json.loads(self._get_cached_bytes(SEC_TICKERS_URL, cache_path))

        for entry in raw.values():
            if str(entry.get("ticker", "")).upper() == target:
                cik = str(entry["cik_str"]).zfill(10)
                return cik, str(entry["title"])
        raise KeyError(f"Ticker '{target}' not found in SEC company_tickers.json.")

    def get_company_facts(self, cik: str) -> dict[str, Any]:
        """Fetch and cache the full XBRL companyfacts JSON for a 10-digit CIK."""
        padded_cik = cik.zfill(10)
        url = SEC_COMPANY_FACTS_URL.format(cik=padded_cik)
        cache_path = self.cache_dir / padded_cik / "companyfacts.json"
        return json.loads(self._get_cached_bytes(url, cache_path))

    @staticmethod
    def _extract_filings_from_batch(
        batch: dict[str, Any],
        *,
        cik: str,
        ticker_upper: str,
        company_name: str,
        allowed_forms: set[str],
        fiscal_years: set[int] | None,
        seen_accessions: set[str],
    ) -> list[FilingMetadata]:
        cik_int = str(int(cik))
        forms = batch.get("form", [])
        accessions = batch.get("accessionNumber", [])
        filing_dates = batch.get("filingDate", [])
        report_dates = batch.get("reportDate", [])
        primary_docs = batch.get("primaryDocument", [])

        extracted: list[FilingMetadata] = []
        for idx, form in enumerate(forms):
            if form not in allowed_forms:
                continue

            accession = str(accessions[idx])
            if accession in seen_accessions:
                continue

            report_date_str = report_dates[idx] or filing_dates[idx]
            period_end = date.fromisoformat(report_date_str)
            filing_dt = date.fromisoformat(filing_dates[idx])
            fy = period_end.year

            if fiscal_years is not None and fy not in fiscal_years:
                continue

            seen_accessions.add(accession)
            primary_doc = str(primary_docs[idx])
            filing_url = SEC_ARCHIVES_DOC_URL.format(
                cik_int=cik_int,
                accession_nodash=accession.replace("-", ""),
                primary_doc=primary_doc,
            )
            if form.startswith("10-Q"):
                quarter_num = min(3, max(1, ((period_end.month - 1) // 3) + 1))
                fiscal_period = f"Q{quarter_num}"
            else:
                fiscal_period = "FY"
            extracted.append(
                FilingMetadata(
                    accession_number=accession,
                    cik=cik,
                    ticker=ticker_upper,
                    company_name=company_name,
                    form_type=form,
                    fiscal_year=fy,
                    fiscal_period=fiscal_period,
                    filing_date=filing_dt,
                    period_end_date=period_end,
                    primary_document=primary_doc,
                    filing_url=filing_url,
                    is_amendment=form.endswith("/A"),
                )
            )
        return extracted

    def get_filings(
        self,
        ticker: str,
        fiscal_years: set[int] | None = None,
        form_types: tuple[str, ...] = ("10-K",),
        include_amendments: bool = True,
    ) -> list[FilingMetadata]:
        """List 10-K and/or 10-Q filings for a ticker, paginating archive files if needed."""
        cik, company_name = self.get_cik_for_ticker(ticker)
        url = SEC_SUBMISSIONS_URL.format(cik=cik)
        cache_path = self.cache_dir / cik / "submissions.json"
        submissions = json.loads(self._get_cached_bytes(url, cache_path))

        ticker_upper = ticker.strip().upper()
        allowed_forms: set[str] = set()
        for ft in form_types:
            base = ft.strip().upper()
            allowed_forms.add(base)
            if include_amendments and not base.endswith("/A"):
                allowed_forms.add(f"{base}/A")
        seen_accessions: set[str] = set()

        filings_obj = submissions.get("filings", {})
        results = self._extract_filings_from_batch(
            filings_obj.get("recent", {}),
            cik=cik,
            ticker_upper=ticker_upper,
            company_name=company_name,
            allowed_forms=allowed_forms,
            fiscal_years=fiscal_years,
            seen_accessions=seen_accessions,
        )

        # High-volume filers (e.g. JPM, GS, BAC) push filings past `recent` into `files` archives.
        archive_files: list[dict[str, Any]] = filings_obj.get("files", []) or []
        found_years = {f.fiscal_year for f in results}
        needs_archive = (
            fiscal_years is None
            or not fiscal_years.issubset(found_years)
            or any(ft.startswith("10-Q") for ft in allowed_forms)
        )

        if needs_archive and archive_files:
            min_filing_year = min(fiscal_years) if fiscal_years else None
            max_filing_year = (max(fiscal_years) + 2) if fiscal_years else None

            for file_meta in archive_files:
                filename = str(file_meta.get("name", "")).strip()
                if not filename:
                    continue

                filing_from_str = file_meta.get("filingFrom")
                filing_to_str = file_meta.get("filingTo")
                if min_filing_year is not None and filing_to_str:
                    try:
                        if date.fromisoformat(filing_to_str).year < min_filing_year:
                            continue
                    except ValueError:
                        pass
                if max_filing_year is not None and filing_from_str:
                    try:
                        if date.fromisoformat(filing_from_str).year > max_filing_year:
                            continue
                    except ValueError:
                        pass

                archive_url = SEC_SUBMISSIONS_ARCHIVE_URL.format(filename=filename)
                archive_cache = self.cache_dir / cik / filename
                archive_batch = json.loads(self._get_cached_bytes(archive_url, archive_cache))
                results.extend(
                    self._extract_filings_from_batch(
                        archive_batch,
                        cik=cik,
                        ticker_upper=ticker_upper,
                        company_name=company_name,
                        allowed_forms=allowed_forms,
                        fiscal_years=fiscal_years,
                        seen_accessions=seen_accessions,
                    )
                )
                found_years = {f.fiscal_year for f in results}
                if (
                    fiscal_years is not None
                    and fiscal_years.issubset(found_years)
                    and not include_amendments
                    and not any(ft.startswith("10-Q") for ft in allowed_forms)
                ):
                    break

        return results

    def get_10k_filings(
        self,
        ticker: str,
        fiscal_years: set[int] | None = None,
        include_amendments: bool = True,
    ) -> list[FilingMetadata]:
        """List 10-K (and optionally 10-K/A) filings for a ticker."""
        return self.get_filings(
            ticker,
            fiscal_years=fiscal_years,
            form_types=("10-K",),
            include_amendments=include_amendments,
        )

    def download_filing_html(self, filing: FilingMetadata) -> Path:
        """Download and cache the primary HTML document for a filing, returning its local path."""
        cache_path = (
            self.cache_dir
            / filing.cik
            / filing.accession_number.replace("-", "")
            / filing.primary_document
        )
        self._get_cached_bytes(filing.filing_url, cache_path)
        return cache_path
