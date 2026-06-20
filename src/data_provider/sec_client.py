"""SEC EDGAR client for fundamentals, Form 4 insider, 13F flows."""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_HEADERS = {"User-Agent": "us-stock-ai research@example.com", "Accept-Encoding": "gzip, deflate"}


def _cache_path(key: str) -> Path:
    safe = key.replace("/", "_").replace(":", "_")
    return _CACHE_DIR / f"sec_{safe}.json"


def _load_cache(key: str, max_age_hours: int = 24) -> dict | None:
    p = _cache_path(key)
    if not p.exists():
        return None
    age = (time.time() - p.stat().st_mtime) / 3600
    if age > max_age_hours:
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _save_cache(key: str, data: dict) -> None:
    _cache_path(key).write_text(json.dumps(data, default=str))


def _get(url: str, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("SEC GET attempt %d failed: %s — %s", attempt + 1, url, e)
            time.sleep(2 ** attempt)
    return {}


def get_company_facts(cik: str) -> dict[str, Any]:
    """Fetch SEC XBRL company facts (revenue, EPS, etc.)."""
    key = f"facts_{cik}_{date.today().strftime('%Y-%m')}"
    cached = _load_cache(key, max_age_hours=72)
    if cached:
        return cached

    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"
    data = _get(url)
    if data:
        _save_cache(key, data)
    return data


def search_company_cik(ticker: str) -> str | None:
    """Resolve ticker -> CIK via SEC EDGAR company search."""
    key = f"cik_{ticker}"
    cached = _load_cache(key, max_age_hours=168)
    if cached:
        return cached.get("cik")

    url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2020-01-01&forms=10-K"
    # Use the company tickers JSON for fast lookup
    tickers_url = "https://www.sec.gov/files/company_tickers.json"
    key2 = "company_tickers_map"
    tickers_map = _load_cache(key2, max_age_hours=168)
    if not tickers_map:
        tickers_map = _get(tickers_url)
        if tickers_map:
            _save_cache(key2, tickers_map)

    if tickers_map:
        for entry in tickers_map.values():
            if isinstance(entry, dict) and entry.get("ticker", "").upper() == ticker.upper():
                cik = str(entry["cik_str"])
                _save_cache(key, {"cik": cik})
                return cik
    return None


def get_recent_filings(cik: str, form_type: str = "10-K", limit: int = 5) -> list[dict]:
    """Return recent filings for a CIK."""
    key = f"filings_{cik}_{form_type}_{date.today().strftime('%Y-%m')}"
    cached = _load_cache(key, max_age_hours=48)
    if cached:
        return cached.get("filings", [])

    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    data = _get(url)
    if not data:
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])

    results = []
    for form, d, acc in zip(forms, dates, accessions):
        if form == form_type:
            results.append({"form": form, "date": d, "accession": acc})
            if len(results) >= limit:
                break

    _save_cache(key, {"filings": results})
    return results


def extract_revenue_yoy(facts: dict) -> float | None:
    """Extract latest YoY revenue growth from SEC company facts."""
    try:
        revenues = (
            facts.get("facts", {})
            .get("us-gaap", {})
            .get("Revenues", {})
            .get("units", {})
            .get("USD", [])
        )
        if not revenues:
            revenues = (
                facts.get("facts", {})
                .get("us-gaap", {})
                .get("RevenueFromContractWithCustomerExcludingAssessedTax", {})
                .get("units", {})
                .get("USD", [])
            )
        # Filter annual (10-K) entries
        annual = [r for r in revenues if r.get("form") == "10-K"]
        if len(annual) < 2:
            return None
        annual.sort(key=lambda x: x.get("end", ""), reverse=True)
        latest = annual[0]["val"]
        prior = annual[1]["val"]
        if prior == 0:
            return None
        return (latest - prior) / abs(prior)
    except Exception:
        return None
