"""US-market rules ported from VALIDATED Taiwan-stock backtest lessons.

Source: trading_knowledge_hub, domain=taiwan_stock, status=backtest_supported.
These are empirically-supported behavioural edges (market-agnostic), translated
to US equities. Shadow/advisory — they annotate signals, they don't change the
composite grade.

Key validated findings ported here:
1. 進場條件保護 (entry-condition protection): signals where you did NOT chase the
   breakout returned +10.3% / 76.5% win vs 0.9%/48% overall. → Reward good entry
   (pullback to rising support), penalise chasing (extended + overbought).
2. 三大失敗模式 (three failure modes):
   - 題材失靈 (theme fizzle, -8.6%): news hype NOT backed by fundamentals.
   - 進場後轉弱 (post-entry weakness, -9.7%): no volume/price follow-through.
   - 停損觸發 (stop hit, -8.6%): stop distance not matched to volatility.
3. 高報酬低勝率題材是追高陷阱 (e.g. HBM 8.1% return but <50% win) → wait pullback.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _rsi(close: pd.Series, n: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not rsi.empty and not np.isnan(rsi.iloc[-1]) else 50.0


def entry_quality(ohlcv: pd.DataFrame) -> dict[str, Any]:
    """Port of 進場條件保護: classify entry timing to avoid chasing.

    Returns {label, reason, chase_risk}. Labels:
    - 可進場   : pulled back to rising 50MA, healthy RSI — the +10.3% sweet spot
    - 等拉回   : strong trend but extended; wait for a dip
    - 避免追高 : near 52w high AND overbought — classic chase trap
    - 觀望     : below trend / no setup
    """
    if ohlcv is None or ohlcv.empty or len(ohlcv) < 50:
        return {"label": "觀望", "reason": "資料不足", "chase_risk": False}

    close = ohlcv["Close"].astype(float)
    high = ohlcv["High"].astype(float)
    price = float(close.iloc[-1])
    ma50 = float(close.tail(50).mean())
    ma20 = float(close.tail(20).mean())
    rsi = _rsi(close)
    hi_52w = float(high.tail(252).max()) if len(high) >= 252 else float(high.max())
    pct_from_high = (price - hi_52w) / hi_52w * 100
    above_trend = price > ma50

    # 避免追高: near 52w high and overbought → chasing trap (HBM lesson)
    if pct_from_high >= -3 and rsi >= 70:
        return {"label": "避免追高", "reason": f"逼近52週高({pct_from_high:.0f}%)且RSI {rsi:.0f} 過熱，等拉回",
                "chase_risk": True}
    # 等拉回: strong uptrend but extended above short MA
    if above_trend and price > ma20 * 1.08 and rsi >= 65:
        return {"label": "等拉回", "reason": f"趨勢強但短線乖離大(RSI {rsi:.0f})，回測支撐再進",
                "chase_risk": True}
    # 可進場: above rising 50MA, pulled back to healthy RSI — the protected sweet spot
    if above_trend and 40 <= rsi <= 62:
        return {"label": "可進場", "reason": f"站上50MA且RSI {rsi:.0f} 健康未過熱（進場保護區）",
                "chase_risk": False}
    if above_trend:
        return {"label": "等拉回", "reason": f"站上趨勢但RSI {rsi:.0f}，擇機進場", "chase_risk": False}
    return {"label": "觀望", "reason": "未站上50MA趨勢", "chase_risk": False}


def failure_mode_risks(
    fundamental_score: int,
    news_catalyst_score: int,
    atr_pct: float,
    rsi: float | None = None,
) -> list[dict[str, str]]:
    """Port of 三大失敗模式: flag setups resembling validated failure patterns."""
    risks: list[dict[str, str]] = []

    # 題材失靈: news hype but weak fundamentals (-8.6% in TW). News高 but 基本面低.
    if news_catalyst_score >= 9 and fundamental_score < 10:
        risks.append({
            "mode": "題材失靈風險",
            "detail": "新聞熱度高但基本面偏弱，題材退潮易回跌（台股驗證 -8.6%）",
        })

    # 停損觸發: excessive volatility → stop easily shaken out (-8.6%, 100% stop-hit).
    if atr_pct >= 5:
        risks.append({
            "mode": "停損風險",
            "detail": f"波動過大(ATR {atr_pct:.1f}%)，停損距離難匹配，易開盤被洗（台股驗證 100% 觸損）",
        })

    return risks


def entry_quality_from_ohlcv_and_score(ohlcv: pd.DataFrame, score) -> dict[str, Any]:
    """Convenience: bundle entry_quality + failure risks for one StockScore."""
    eq = entry_quality(ohlcv)
    close = ohlcv["Close"].astype(float) if ohlcv is not None and not ohlcv.empty else None
    rsi = _rsi(close) if close is not None else None
    risks = failure_mode_risks(
        getattr(score, "fundamental_score", 0),
        getattr(score, "news_catalyst_score", 0),
        getattr(score, "atr_pct", 0.0) or 0.0,
        rsi,
    )
    return {"entry_quality": eq, "failure_risks": risks}
