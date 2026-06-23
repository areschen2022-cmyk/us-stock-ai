"""yfinance wrapper with caching and retry logic."""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(key: str) -> Path:
    safe = key.replace("/", "_").replace(":", "_")
    return _CACHE_DIR / f"yf_{safe}.json"


def _load_cache(key: str, max_age_hours: int = 12) -> dict | None:
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


def fetch_ohlcv(symbol: str, period: str = "6mo") -> pd.DataFrame:
    """Return OHLCV DataFrame for symbol."""
    cache_key = f"ohlcv_{symbol}_{period}_{date.today()}"
    cached = _load_cache(cache_key, max_age_hours=8)
    if cached:
        return pd.DataFrame(cached)

    for attempt in range(3):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, auto_adjust=True)
            if df.empty:
                logger.warning("Empty OHLCV for %s", symbol)
                return pd.DataFrame()
            df.index = df.index.strftime("%Y-%m-%d")
            _save_cache(cache_key, df.to_dict())
            return df
        except Exception as e:
            logger.warning("yfinance OHLCV attempt %d failed for %s: %s", attempt + 1, symbol, e)
            time.sleep(2 ** attempt)
    return pd.DataFrame()


def fetch_info(symbol: str) -> dict[str, Any]:
    """Return ticker .info dict (fundamentals, sector, etc.)."""
    cache_key = f"info_{symbol}_{date.today()}"
    cached = _load_cache(cache_key, max_age_hours=20)
    if cached:
        return cached

    for attempt in range(3):
        try:
            info = yf.Ticker(symbol).info
            _save_cache(cache_key, info)
            return info
        except Exception as e:
            logger.warning("yfinance info attempt %d failed for %s: %s", attempt + 1, symbol, e)
            time.sleep(2 ** attempt)
    return {}


def fetch_batch_ohlcv(symbols: list[str], period: str = "2y") -> dict[str, pd.DataFrame]:
    """Batch download OHLCV for multiple symbols.

    2y window (~504 bars) supports US-strategy indicators that need long history:
    200-day SMA, 63-day RS slope, 6/12-month momentum, and a clean 52-week range.
    Existing technical_score still uses tail() slices so more history is harmless.
    """
    cache_key = f"batch_{'_'.join(sorted(symbols)[:5])}_{period}_{date.today()}"
    if len(symbols) <= 10:
        # Small batch: cache individually
        return {sym: fetch_ohlcv(sym, period) for sym in symbols}

    try:
        raw = yf.download(symbols, period=period, auto_adjust=True, group_by="ticker", progress=False)
        result: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            try:
                df = raw[sym].dropna(how="all")
                df.index = df.index.strftime("%Y-%m-%d")
                result[sym] = df
            except Exception:
                result[sym] = pd.DataFrame()
        return result
    except Exception as e:
        logger.error("Batch download failed: %s — falling back to individual", e)
        return {sym: fetch_ohlcv(sym, period) for sym in symbols}


def fetch_market_indices() -> dict[str, float]:
    """Return latest close prices for major indices/ETFs."""
    symbols = ["SPY", "QQQ", "IWM", "SMH", "XLF", "XLK", "XLE", "XLV", "TLT", "HYG", "^VIX"]
    result: dict[str, float] = {}
    for sym in symbols:
        try:
            df = fetch_ohlcv(sym, period="5d")
            if not df.empty:
                result[sym] = float(df["Close"].iloc[-1])
        except Exception:
            pass
    return result


def fetch_earnings_calendar(symbol: str) -> dict[str, Any]:
    """Return next earnings date if available."""
    try:
        cal = yf.Ticker(symbol).calendar
        if cal is None:
            return {}
        if isinstance(cal, dict):
            return cal
        return cal.to_dict() if hasattr(cal, "to_dict") else {}
    except Exception:
        return {}
