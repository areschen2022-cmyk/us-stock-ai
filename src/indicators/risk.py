"""Risk scoring — earnings proximity, gap risk, SEC flags."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any


def risk_score(
    atr_pct: float,
    earnings_cal: dict[str, Any],
    info: dict[str, Any],
    today: date | None = None,
) -> tuple[int, list[str]]:
    """
    Returns (penalty 0-10, risk reasons list).
    Higher return = MORE risk = lower final score.
    """
    penalty = 0
    reasons: list[str] = []
    today = today or date.today()

    # Earnings proximity (within 7 days = high gap risk)
    earnings_date = None
    try:
        raw = earnings_cal.get("Earnings Date") or earnings_cal.get("earningsDate")
        if raw:
            if isinstance(raw, (list, tuple)):
                raw = raw[0]
            if isinstance(raw, datetime):
                earnings_date = raw.date()
            elif isinstance(raw, date):
                earnings_date = raw
            elif isinstance(raw, str):
                earnings_date = datetime.fromisoformat(raw).date()
    except Exception:
        pass

    if earnings_date:
        days_to_earnings = (earnings_date - today).days
        if 0 <= days_to_earnings <= 5:
            penalty += 6
            reasons.append(f"財報 {days_to_earnings} 日內 — 跳空風險高")
        elif 0 <= days_to_earnings <= 14:
            penalty += 3
            reasons.append(f"財報將近（{days_to_earnings} 日）")

    # ATR overextension
    if atr_pct > 5:
        penalty += 3
        reasons.append(f"波動過大 ATR {atr_pct:.1f}%")
    elif atr_pct > 3:
        penalty += 1
        reasons.append(f"波動偏高 ATR {atr_pct:.1f}%")

    # Short float (>20% = short-squeeze risk / danger)
    short_float = info.get("shortPercentOfFloat")
    if short_float and short_float > 0.2:
        penalty += 2
        reasons.append(f"高放空比 {short_float*100:.0f}%")

    # Market cap: micro-cap risk
    market_cap = info.get("marketCap", 0)
    if market_cap and market_cap < 500_000_000:
        penalty += 2
        reasons.append("微型股風險（市值 < $5億）")

    return min(penalty, 10), reasons
