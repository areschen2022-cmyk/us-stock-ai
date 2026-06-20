"""Technical indicators for US stocks."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _rsi(close: pd.Series, n: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not rsi.empty else 50.0


def technical_score(df: pd.DataFrame, spy_df: pd.DataFrame | None = None) -> tuple[int, list[str]]:
    """
    Returns (score 0-30, reasons list).
    Evaluates: MA alignment, 52W high proximity, volume surge, RS vs SPY, ATR.
    """
    if df.empty or len(df) < 20:
        return 0, ["insufficient data"]

    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)

    score = 0
    reasons: list[str] = []

    price = float(close.iloc[-1])

    # Moving averages
    ma20 = float(_ema(close, 20).iloc[-1])
    ma50 = float(_ema(close, 50).iloc[-1])
    ma150 = float(_ema(close, 150).iloc[-1]) if len(close) >= 150 else ma50
    ma200 = float(_ema(close, 200).iloc[-1]) if len(close) >= 200 else ma150

    bullish_ma = price > ma50 > ma150 > ma200
    if bullish_ma:
        score += 8
        reasons.append("MA alignment bullish (50>150>200)")
    elif price > ma50:
        score += 4
        reasons.append("Above 50MA")
    elif price < ma50:
        reasons.append("Below 50MA")

    # 52-week high proximity
    high_52w = float(high.tail(252).max()) if len(high) >= 252 else float(high.max())
    pct_from_high = (price - high_52w) / high_52w * 100
    if pct_from_high >= -5:
        score += 6
        reasons.append(f"Near 52W high ({pct_from_high:.1f}%)")
    elif pct_from_high >= -15:
        score += 3
        reasons.append(f"Within 15% of 52W high ({pct_from_high:.1f}%)")

    # Volume surge (vs 20-day avg)
    avg_vol_20 = float(volume.tail(20).mean())
    last_vol = float(volume.iloc[-1])
    vol_ratio = last_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0
    if vol_ratio >= 2.0:
        score += 6
        reasons.append(f"Volume surge {vol_ratio:.1f}x avg")
    elif vol_ratio >= 1.3:
        score += 3
        reasons.append(f"Above-avg volume {vol_ratio:.1f}x")

    # RSI
    rsi = _rsi(close)
    if 50 <= rsi <= 70:
        score += 4
        reasons.append(f"RSI healthy ({rsi:.0f})")
    elif rsi > 70:
        reasons.append(f"RSI overbought ({rsi:.0f})")
    elif rsi < 35:
        reasons.append(f"RSI oversold ({rsi:.0f})")

    # Relative strength vs SPY (20d)
    if spy_df is not None and not spy_df.empty and len(spy_df) >= 20:
        spy_close = spy_df["Close"].astype(float)
        rs_stock = (close.iloc[-1] / close.iloc[-20] - 1) * 100
        rs_spy = (spy_close.iloc[-1] / spy_close.iloc[-20] - 1) * 100
        rel_str = rs_stock - rs_spy
        if rel_str > 5:
            score += 6
            reasons.append(f"Outperforming SPY +{rel_str:.1f}%")
        elif rel_str > 0:
            score += 2
            reasons.append(f"Slightly ahead of SPY +{rel_str:.1f}%")
        else:
            reasons.append(f"Underperforming SPY {rel_str:.1f}%")

    return min(score, 30), reasons


def calc_atr_pct(df: pd.DataFrame, n: int = 14) -> float:
    """ATR as % of price — used for risk/stop sizing."""
    if df.empty or len(df) < n:
        return 0.0
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(n).mean().iloc[-1])
    price = float(close.iloc[-1])
    return atr / price * 100 if price > 0 else 0.0
