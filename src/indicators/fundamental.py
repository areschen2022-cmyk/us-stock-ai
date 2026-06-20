"""Fundamental scoring from yfinance info + SEC data."""
from __future__ import annotations

from typing import Any


def fundamental_score(info: dict[str, Any], revenue_yoy: float | None = None) -> tuple[int, list[str]]:
    """
    Returns (score 0-20, reasons list).
    Uses yfinance .info + optional SEC revenue YoY growth.
    """
    score = 0
    reasons: list[str] = []

    if not info:
        return 0, ["no fundamental data"]

    # Revenue growth (prefer SEC, fallback to yfinance)
    if revenue_yoy is not None:
        rev_pct = revenue_yoy * 100
        if rev_pct >= 25:
            score += 5
            reasons.append(f"Revenue YoY +{rev_pct:.0f}%")
        elif rev_pct >= 10:
            score += 3
            reasons.append(f"Revenue YoY +{rev_pct:.0f}%")
        elif rev_pct < 0:
            reasons.append(f"Revenue declining {rev_pct:.0f}%")
    else:
        rev_growth = info.get("revenueGrowth")
        if rev_growth is not None:
            pct = rev_growth * 100
            if pct >= 20:
                score += 4
                reasons.append(f"Revenue growth {pct:.0f}%")
            elif pct >= 8:
                score += 2
                reasons.append(f"Revenue growth {pct:.0f}%")

    # Gross margin
    gross_margin = info.get("grossMargins")
    if gross_margin is not None:
        pct = gross_margin * 100
        if pct >= 60:
            score += 4
            reasons.append(f"Gross margin {pct:.0f}%")
        elif pct >= 40:
            score += 2
            reasons.append(f"Gross margin {pct:.0f}%")

    # Operating margin
    op_margin = info.get("operatingMargins")
    if op_margin is not None:
        pct = op_margin * 100
        if pct >= 20:
            score += 3
            reasons.append(f"Op margin {pct:.0f}%")
        elif pct >= 8:
            score += 1

    # Free cash flow positive
    fcf = info.get("freeCashflow")
    if fcf is not None and fcf > 0:
        score += 3
        reasons.append("Positive FCF")
    elif fcf is not None and fcf < 0:
        reasons.append("Negative FCF")

    # Debt-to-equity
    de = info.get("debtToEquity")
    if de is not None:
        if de < 50:
            score += 2
            reasons.append(f"Low D/E {de:.0f}")
        elif de > 200:
            reasons.append(f"High leverage D/E {de:.0f}")

    # EPS trailing
    eps = info.get("trailingEps")
    if eps is not None and eps > 0:
        score += 3
        reasons.append(f"EPS profitable (${eps:.2f})")
    elif eps is not None and eps < 0:
        reasons.append(f"EPS negative (${eps:.2f})")

    return min(score, 20), reasons
