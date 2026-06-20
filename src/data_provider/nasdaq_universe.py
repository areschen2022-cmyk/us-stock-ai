"""Fetch and maintain the Nasdaq stock symbol universe."""
from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


def _fetch_text(url: str) -> str:
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            return r.text
        except Exception as e:
            logger.warning("Attempt %d failed: %s", attempt + 1, e)
            time.sleep(2 ** attempt)
    return ""


def load_universe(min_market_cap_b: float = 1.0, exclude: list[str] | None = None) -> list[str]:
    """Return deduplicated list of common stock symbols."""
    cache_file = _CACHE_DIR / f"nasdaq_universe_{date.today()}.txt"

    if cache_file.exists():
        symbols = cache_file.read_text().splitlines()
        return [s for s in symbols if s]

    symbols: set[str] = set()
    exclude_set = set(exclude or [])

    # Nasdaq-listed
    text = _fetch_text(_NASDAQ_URL)
    if text:
        reader = csv.DictReader(io.StringIO(text), delimiter="|")
        for row in reader:
            sym = row.get("Symbol", "").strip()
            test = row.get("Test Issue", "").strip()
            etf = row.get("ETF", "").strip()
            if sym and test == "N" and etf == "N" and "$" not in sym and len(sym) <= 5:
                symbols.add(sym)

    # Other-listed (NYSE, AMEX)
    text = _fetch_text(_OTHER_URL)
    if text:
        reader = csv.DictReader(io.StringIO(text), delimiter="|")
        for row in reader:
            sym = row.get("ACT Symbol", "").strip()
            test = row.get("Test Issue", "").strip()
            etf = row.get("ETF", "").strip()
            if sym and test == "N" and etf == "N" and "$" not in sym and len(sym) <= 5:
                symbols.add(sym)

    final = sorted(symbols - exclude_set)
    cache_file.write_text("\n".join(final))
    logger.info("Universe loaded: %d symbols", len(final))
    return final
