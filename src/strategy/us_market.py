"""US-market strategy skeletons (SHADOW MODE).

These functions implement US-specific momentum/quality rules sourced from the
Minervini Trend Template and retail momentum-system research. They are designed
to run in *shadow*: computed and displayed for data accumulation, but NOT yet
wired into the live grade until validated against forward returns.

References (2026-06 research):
- Minervini Trend Template: 7/8 of 8 criteria, 50>150>200 SMA cascade,
  price >=30% above 52w low and within 25% of 52w high, RS slope >= +0.15
- RS rating: 63-day linear-regression slope of (stock/SPY) ratio,
  score = clip((slope + 0.3)/0.6 * 10, 0, 10)
- Liquidity: avg daily dollar volume >= $5M (50d) OR >= 300k shares; price >= $10
- Market regime: SPY above 200-day MA AND >=15% of universe in Phase-2 uptrend
- Stop: max(entry - 2*ATR, 20d swing-low * 0.98); require >= 2:1 reward/risk
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


# ── tunable defaults (override via config.yaml: strategy_us) ──────────────────
DEFAULTS: dict[str, Any] = {
    "mode": "shadow",            # shadow | active
    "min_price": 10.0,           # exclude sub-$10 names
    "min_dollar_vol": 5_000_000, # 50d avg daily dollar volume
    "min_shares": 300_000,       # alt liquidity gate (50d avg shares)
    "rs_lookback": 63,           # 3-month RS window
    "regime_breadth_pct": 15.0,  # % of universe in Phase 2 to allow new entries
    "atr_mult": 2.0,             # ATR stop multiplier
    "swing_low_lookback": 20,    # swing-low stop lookback
    "swing_low_buffer": 0.98,    # 2% buffer under swing low
    "min_reward_risk": 2.0,      # minimum reward:risk to flag a tradeable setup
}


def _sma(series: pd.Series, n: int) -> float | None:
    if len(series) < n:
        return None
    return float(series.tail(n).mean())


# ── Relative Strength (63-day RS-line slope) ─────────────────────────────────

def rs_rating_63d(
    stock_close: pd.Series,
    spy_close: pd.Series,
    lookback: int = 63,
) -> dict[str, Any]:
    """Compute the RS line (stock/SPY) and its normalized regression slope.

    Returns {rs_slope, rs_score_0_10, ret_6m, ret_12m_skip1}. The momentum
    returns are exposed so the dashboard can build a cross-sectional RS
    percentile (IBD-style 1-99) across the whole universe.
    """
    out: dict[str, Any] = {
        "rs_slope": None, "rs_score_0_10": None,
        "ret_6m": None, "ret_12m_skip1": None,
    }
    if stock_close is None or spy_close is None:
        return out
    s = stock_close.astype(float).dropna()
    spy = spy_close.astype(float).dropna()
    n = min(len(s), len(spy))
    if n < lookback + 1:
        return out

    # RS line = stock / SPY, aligned on the last `lookback` bars
    rs_line = (s.iloc[-lookback:].values / spy.iloc[-lookback:].values)
    rs_line = rs_line / rs_line[0]  # normalize to 1.0 at window start
    x = np.arange(lookback)
    # slope per ~quarter; scale so a +30% RS-line rise over the window ≈ +0.3
    slope = float(np.polyfit(x, rs_line, 1)[0]) * lookback
    out["rs_slope"] = round(slope, 4)
    out["rs_score_0_10"] = round(float(np.clip((slope + 0.3) / 0.6 * 10, 0, 10)), 2)

    # Momentum returns for cross-sectional ranking
    if len(s) >= 126:
        out["ret_6m"] = round(float(s.iloc[-1] / s.iloc[-126] - 1) * 100, 2)
    if len(s) >= 252 + 21:
        out["ret_12m_skip1"] = round(float(s.iloc[-21] / s.iloc[-(252 + 21)] - 1) * 100, 2)
    return out


def rs_percentile(composite_returns: dict[str, float]) -> dict[str, int]:
    """Cross-sectional RS rating (1-99) from a {symbol: composite_return} map.
    Composite return = 0.5*ret_6m + 0.5*ret_12m_skip1 (fallbacks handled by caller)."""
    if not composite_returns:
        return {}
    items = sorted(composite_returns.items(), key=lambda kv: kv[1])
    n = len(items)
    out: dict[str, int] = {}
    for rank, (sym, _) in enumerate(items):
        pct = int(round((rank + 1) / n * 98)) + 1  # 1..99
        out[sym] = min(99, max(1, pct))
    return out


# ── Minervini Trend Template (8 criteria) ────────────────────────────────────

def minervini_trend_template(
    df: pd.DataFrame,
    rs_rating: int | None = None,
) -> dict[str, Any]:
    """Return {pass_count 0-8, flags{...}, phase2: bool}. Needs >=200 bars for
    full evaluation; gracefully degrades (criteria needing 200MA fail if short)."""
    flags: dict[str, bool] = {}
    if df is None or df.empty or len(df) < 50:
        return {"pass_count": 0, "flags": flags, "phase2": False, "evaluable": False}

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    price = float(close.iloc[-1])

    sma50 = _sma(close, 50)
    sma150 = _sma(close, 150)
    sma200 = _sma(close, 200)
    sma200_22ago = None
    if len(close) >= 222:
        sma200_22ago = float(close.iloc[-222:-22].mean()) if len(close.iloc[-222:-22]) >= 1 else None

    lo_52w = float(low.tail(252).min()) if len(low) >= 252 else float(low.min())
    hi_52w = float(high.tail(252).max()) if len(high) >= 252 else float(high.max())

    # 1. price above 150 & 200 SMA
    flags["c1_above_150_200"] = bool(sma150 and sma200 and price > sma150 and price > sma200)
    # 2. 150 SMA > 200 SMA
    flags["c2_150_above_200"] = bool(sma150 and sma200 and sma150 > sma200)
    # 3. 200 SMA trending up (>= ~1 month)
    flags["c3_200_trending_up"] = bool(sma200 and sma200_22ago and sma200 > sma200_22ago)
    # 4. 50 > 150 > 200 cascade
    flags["c4_cascade"] = bool(sma50 and sma150 and sma200 and sma50 > sma150 > sma200)
    # 5. price above 50 SMA
    flags["c5_above_50"] = bool(sma50 and price > sma50)
    # 6. price >= 30% above 52w low
    flags["c6_30pct_above_low"] = bool(lo_52w > 0 and price >= lo_52w * 1.30)
    # 7. price within 25% of 52w high
    flags["c7_within_25pct_high"] = bool(hi_52w > 0 and price >= hi_52w * 0.75)
    # 8. RS rating >= 70 (if provided)
    flags["c8_rs_70"] = bool(rs_rating is not None and rs_rating >= 70)

    pass_count = sum(1 for v in flags.values() if v)
    # Phase 2 = passes >=7/8 (RS optional when not provided -> require 6/7 core)
    core_pass = pass_count - (1 if flags["c8_rs_70"] else 0)
    phase2 = pass_count >= 7 if rs_rating is not None else core_pass >= 6
    return {"pass_count": pass_count, "flags": flags, "phase2": phase2, "evaluable": True}


# ── Liquidity gate ───────────────────────────────────────────────────────────

def liquidity_gate(df: pd.DataFrame, price: float | None, cfg: dict | None = None) -> dict[str, Any]:
    """Dollar-volume + price filter. Returns {passed, dollar_vol_50d, reason}."""
    c = {**DEFAULTS, **(cfg or {})}
    if df is None or df.empty or price is None:
        return {"passed": False, "dollar_vol_50d": None, "reason": "無資料"}
    close = df["Close"].astype(float)
    vol = df["Volume"].astype(float)
    n = min(50, len(df))
    avg_shares = float(vol.tail(n).mean())
    avg_dollar = float((close.tail(n) * vol.tail(n)).mean())

    reasons: list[str] = []
    if price < c["min_price"]:
        reasons.append(f"股價 < ${c['min_price']:.0f}")
    liquid = avg_dollar >= c["min_dollar_vol"] or avg_shares >= c["min_shares"]
    if not liquid:
        reasons.append(f"日成交額 ${avg_dollar/1e6:.1f}M < ${c['min_dollar_vol']/1e6:.0f}M")
    passed = price >= c["min_price"] and liquid
    return {
        "passed": passed,
        "dollar_vol_50d": round(avg_dollar, 0),
        "avg_shares_50d": round(avg_shares, 0),
        "reason": "通過" if passed else "、".join(reasons),
    }


# ── Market regime ────────────────────────────────────────────────────────────

def market_regime(
    spy_df: pd.DataFrame | None,
    breadth_phase2_pct: float | None,
    cfg: dict | None = None,
) -> dict[str, Any]:
    """SPY-above-200MA + breadth gate. Returns regime label and whether new
    long entries are permitted. Complements the VIX-based 風向 in dashboard."""
    c = {**DEFAULTS, **(cfg or {})}
    spy_above_200 = None
    if spy_df is not None and not spy_df.empty and len(spy_df) >= 200:
        close = spy_df["Close"].astype(float)
        spy_above_200 = bool(float(close.iloc[-1]) > float(close.tail(200).mean()))

    breadth_ok = breadth_phase2_pct is not None and breadth_phase2_pct >= c["regime_breadth_pct"]
    allow = bool(spy_above_200) and bool(breadth_ok)
    if spy_above_200 is None:
        label = "未知"
    elif allow:
        label = "可進場（多頭）"
    elif spy_above_200:
        label = "謹慎（廣度不足）"
    else:
        label = "防禦（SPY 跌破 200MA）"
    return {
        "regime": label,
        "spy_above_200ma": spy_above_200,
        "breadth_phase2_pct": breadth_phase2_pct,
        "allow_new_entries": allow,
    }


# ── Conservative stop + reward/risk + sizing ─────────────────────────────────

def conservative_stop(
    df: pd.DataFrame,
    price: float | None,
    atr_pct: float,
    cfg: dict | None = None,
) -> dict[str, Any]:
    """Stop = max(price - atr_mult*ATR, 20d swing-low*buffer). Higher = more
    conservative. Returns {stop, method, risk_pct}."""
    c = {**DEFAULTS, **(cfg or {})}
    if df is None or df.empty or not price:
        return {"stop": None, "method": None, "risk_pct": None}
    atr_abs = price * atr_pct / 100.0
    atr_stop = price - c["atr_mult"] * atr_abs

    low = df["Low"].astype(float)
    n = min(c["swing_low_lookback"], len(df))
    swing_stop = float(low.tail(n).min()) * c["swing_low_buffer"]

    if swing_stop >= atr_stop:
        stop, method = swing_stop, "swing_low"
    else:
        stop, method = atr_stop, "atr"
    stop = round(stop, 2)
    risk_pct = round((price - stop) / price * 100, 2) if price else None
    return {"stop": stop, "method": method, "risk_pct": risk_pct}


def reward_risk_ok(entry: float, stop: float, target: float, cfg: dict | None = None) -> bool:
    c = {**DEFAULTS, **(cfg or {})}
    if not (entry and stop and target) or entry <= stop:
        return False
    rr = (target - entry) / (entry - stop)
    return rr >= c["min_reward_risk"]


def position_size(equity: float, risk_pct: float, entry: float, stop: float) -> dict[str, Any]:
    """1-2% rule: shares = (equity * risk_pct%) / (entry - stop)."""
    if not (equity and entry and stop) or entry <= stop:
        return {"shares": 0, "dollar_risk": 0.0, "position_value": 0.0}
    dollar_risk = equity * risk_pct / 100.0
    per_share_risk = entry - stop
    shares = int(dollar_risk // per_share_risk)
    return {
        "shares": shares,
        "dollar_risk": round(dollar_risk, 2),
        "position_value": round(shares * entry, 2),
    }
