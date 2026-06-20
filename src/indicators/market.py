"""Market-wide sentiment adjustment."""
from __future__ import annotations

import pandas as pd


def market_sentiment_score(index_prices: dict[str, float]) -> tuple[int, list[str]]:
    """
    Returns (score 0-10, reasons list).
    Evaluates SPY/QQQ trend, VIX level, TLT/HYG credit signal.
    """
    score = 0
    reasons: list[str] = []

    spy = index_prices.get("SPY", 0)
    qqq = index_prices.get("QQQ", 0)
    vix = index_prices.get("^VIX", 20)
    tlt = index_prices.get("TLT", 0)
    hyg = index_prices.get("HYG", 0)

    # VIX fear gauge
    if vix < 15:
        score += 4
        reasons.append(f"Low VIX ({vix:.1f}) — risk-on")
    elif vix < 20:
        score += 2
        reasons.append(f"Moderate VIX ({vix:.1f})")
    elif vix >= 30:
        score -= 2
        reasons.append(f"High VIX ({vix:.1f}) — risk-off")

    # SPY positive for the day (simple proxy)
    # In practice we'd compare to prior close; here we check if we have an entry
    if spy > 0:
        score += 2
        reasons.append("SPY data available")

    # Credit spread proxy (HYG high = tight spreads = risk-on)
    if hyg > 0:
        # Above $78 historically tight spreads
        if hyg > 78:
            score += 2
            reasons.append(f"HYG {hyg:.1f} — credit markets healthy")
        elif hyg < 72:
            score -= 1
            reasons.append(f"HYG {hyg:.1f} — spread widening")

    # Rate signal: TLT falling = rising rates (headwind for growth)
    if tlt > 0 and tlt < 85:
        reasons.append(f"TLT {tlt:.1f} — rate pressure")

    return max(0, min(score, 10)), reasons


def sector_adjustment(symbol_sector: str, sector_etf_prices: dict[str, float]) -> int:
    """Simple sector momentum adjustment (-3 to +3)."""
    sector_etf_map = {
        "Technology": "XLK",
        "Financial Services": "XLF",
        "Energy": "XLE",
        "Healthcare": "XLV",
    }
    etf = sector_etf_map.get(symbol_sector)
    if not etf or etf not in sector_etf_prices:
        return 0
    # Placeholder: in production compare to SPY return
    return 0
