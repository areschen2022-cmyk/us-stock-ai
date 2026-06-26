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

    # Institutional ownership — yfinance keys are heldPercentInstitutions /
    # heldPercentInsiders (NOT institutionPercentHeld). Wrong keys previously
    # pinned every stock at the neutral fallback (3) — flow never differentiated.
    inst_pct = info.get("heldPercentInstitutions")
    if inst_pct is not None:
        pct = inst_pct * 100
        if pct >= 70:
            score += 5
            reasons.append(f"法人持股 {pct:.0f}%（高）")
        elif pct >= 50:
            score += 3
            reasons.append(f"法人持股 {pct:.0f}%")
        elif pct >= 30:
            score += 1
            reasons.append(f"法人持股 {pct:.0f}%")

    # Insider ownership (alignment of interests)
    insider_pct = info.get("heldPercentInsiders")
    if insider_pct is not None and insider_pct > 0.03:
        score += 3
        reasons.append(f"內部人持股 {insider_pct*100:.1f}%（利益一致）")

    # Volume accumulation proxy: avg 10d vol vs 3m vol (institutional footprint)
    vol10 = info.get("averageDailyVolume10Day")
    vol3m = info.get("averageDailyVolume3Month")
    if vol10 and vol3m and vol3m > 0:
        ratio = vol10 / vol3m
        if ratio >= 1.3:
            score += 3
            reasons.append(f"近期量能放大 {ratio:.1f}x（資金進駐）")
        elif ratio >= 1.1:
            score += 1

    # Form 4 insider buys (if available via edgartools)
    if insider_data:
        buys = insider_data.get("buys", 0)
        sells = insider_data.get("sells", 0)
        if buys > sells and buys > 0:
            score += 4
            reasons.append(f"內部人買進（Form 4：{buys} 筆）")
        elif sells > buys * 2:
            score -= 3
            reasons.append(f"內部人大量賣出（{sells} 筆）")

    return max(0, min(score, 15)), reasons
