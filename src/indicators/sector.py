"""Sector strength scoring (SHADOW MODE, display-only).

Ported from a user-supplied US-stock-scanner spec (2026-07-03): the 5-step
framework is market → sector → stock → earnings/news → risk, but this
codebase previously jumped straight from market regime to individual stocks
with no sector-relative-strength step at all — fetch_market_indices() was
already pulling SMH/XLF/XLK/XLE/XLV every day, but nothing ever compared a
stock's own sector ETF against SPY or checked its trend.

sector_score() mirrors the spec's two most data-available checks (its other
two — "sector_etf_above_VWAP" and "sector_leaders_confirming_strength" —
need intraday VWAP data this EOD-only system doesn't have):
- sector ETF above its own 20d EMA (trend)
- sector ETF outperforming SPY over 20 trading days (relative strength)
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# yfinance's info["sector"] strings → sector ETF. Covers the 11 GICS sectors;
# the scanner spec's XLC addition is included (Communication Services).
SECTOR_ETF_MAP: dict[str, str] = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Utilities": "XLU",
    "Energy": "XLE",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
}

SECTOR_ETFS: tuple[str, ...] = tuple(sorted(set(SECTOR_ETF_MAP.values())))


def _ema(series: pd.Series, n: int) -> float | None:
    if len(series) < n:
        return None
    return float(series.ewm(span=n, adjust=False).mean().iloc[-1])


def sector_etf_score(etf_ohlcv: pd.DataFrame | None, spy_close: pd.Series | None) -> dict[str, Any]:
    """Score 0-20 for one sector ETF: above-20EMA (10pt) + outperforming SPY
    over 20 trading days (10pt). Returns status strong/neutral/weak matching
    the spec's >=15 / >=8 / <8 thresholds."""
    out: dict[str, Any] = {"score": 0, "above_ema20": None, "rs_vs_spy_20d": None, "status": "未知", "reasons": []}
    if etf_ohlcv is None or etf_ohlcv.empty or len(etf_ohlcv) < 21:
        return out

    close = etf_ohlcv["Close"].astype(float)
    price = float(close.iloc[-1])
    ema20 = _ema(close, 20)
    score = 0
    reasons: list[str] = []

    if ema20 is not None:
        above = price > ema20
        out["above_ema20"] = above
        if above:
            score += 10
            reasons.append(f"站上20日均線({price:.1f}>{ema20:.1f})")
        else:
            reasons.append(f"跌破20日均線({price:.1f}<{ema20:.1f})")

    if spy_close is not None and len(spy_close) >= 21 and len(close) >= 21:
        etf_ret = float(close.iloc[-1] / close.iloc[-21] - 1) * 100
        spy_ret = float(spy_close.iloc[-1] / spy_close.iloc[-21] - 1) * 100
        rel = round(etf_ret - spy_ret, 2)
        out["rs_vs_spy_20d"] = rel
        if rel > 0:
            score += 10
            reasons.append(f"20日跑贏SPY {rel:+.1f}%")
        else:
            reasons.append(f"20日落後SPY {rel:+.1f}%")

    out["score"] = score
    out["reasons"] = reasons
    if score >= 15:
        out["status"] = "強勢"
    elif score >= 8:
        out["status"] = "普通"
    else:
        out["status"] = "偏弱"
    return out


def build_sector_scores(sector_ohlcv: dict[str, pd.DataFrame], spy_close: pd.Series | None) -> dict[str, dict]:
    """{etf_symbol: sector_etf_score(...)} for every fetched sector ETF."""
    return {etf: sector_etf_score(sector_ohlcv.get(etf), spy_close) for etf in SECTOR_ETFS}


def map_stock_to_sector_etf(yf_sector: str | None) -> str | None:
    if not yf_sector:
        return None
    return SECTOR_ETF_MAP.get(yf_sector)
