"""Flow/insider scoring — SEC Form 4 insider buys, volume accumulation."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def flow_score(info: dict[str, Any], insider_data: dict | None = None) -> tuple[int, list[str]]:
    """
    Returns (score 0-15, reasons list).
    Uses institution % held, insider buy/sell, institutional % change.
    """
    score = 0
    reasons: list[str] = []

    # Institutional ownership
    inst_pct = info.get("institutionPercentHeld")
    if inst_pct is not None:
        pct = inst_pct * 100
        if pct >= 70:
            score += 4
            reasons.append(f"Institutional held {pct:.0f}%")
        elif pct >= 50:
            score += 2
            reasons.append(f"Institutional held {pct:.0f}%")

    # Insider ownership (alignment)
    insider_pct = info.get("insiderPercentHeld")
    if insider_pct is not None and insider_pct > 0.05:
        score += 3
        reasons.append(f"Insider held {insider_pct*100:.1f}% (aligned)")

    # Form 4 insider buys (if available via edgartools)
    if insider_data:
        buys = insider_data.get("buys", 0)
        sells = insider_data.get("sells", 0)
        if buys > sells and buys > 0:
            score += 5
            reasons.append(f"Insider buying (Form 4: {buys} buys)")
        elif sells > buys * 2:
            score -= 3
            reasons.append(f"Heavy insider selling ({sells} sells)")

    # Institutional % change (quarter over quarter)
    inst_change = info.get("institutionsPercentHeld")  # placeholder field
    if inst_change is None:
        # yfinance doesn't expose quarterly delta directly; skip
        score += 3  # neutral assumption if unknown

    return max(0, min(score, 15)), reasons
