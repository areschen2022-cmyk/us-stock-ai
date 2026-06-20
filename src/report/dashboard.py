"""Build dashboard JSON payload from today's scores."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from src.scoring.score_engine import StockScore
from src.storage.sqlite_store import SQLiteStore

_DOCS_DIR = Path(__file__).parent.parent.parent / "docs"


def _score_to_card(s: StockScore) -> dict[str, Any]:
    return {
        "symbol": s.symbol,
        "name": s.name,
        "score": s.total_score,
        "grade": s.grade,
        "action": s.action,
        "price": s.price,
        "entry": s.entry_price,
        "stop": s.stop_price,
        "atr_pct": s.atr_pct,
        "themes": s.themes,
        "warnings": s.warnings,
        "matched_headlines": s.matched_headlines[:3],
        "sub": {
            "T": s.technical_score,
            "F": s.fundamental_score,
            "Fl": s.flow_score,
            "N": s.news_catalyst_score,
            "M": s.market_sentiment_score,
            "R": -s.risk_penalty,
        },
        "sector": s.sector,
        "market_cap": s.market_cap,
    }


def build_dashboard_json(
    scores: list[StockScore],
    market_prices: dict[str, float],
    open_signals: list[dict],
    ai_reviews: dict[str, dict],
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    sorted_scores = sorted(scores, key=lambda s: s.total_score, reverse=True)

    cards = [_score_to_card(s) for s in sorted_scores]

    # Theme aggregation
    theme_counts: dict[str, list[dict]] = {}
    for s in sorted_scores:
        for t in s.themes:
            theme_counts.setdefault(t, []).append(
                {"symbol": s.symbol, "score": s.total_score}
            )

    return {
        "generated_at": str(today),
        "market": {
            "SPY": market_prices.get("SPY"),
            "QQQ": market_prices.get("QQQ"),
            "VIX": market_prices.get("^VIX"),
            "TLT": market_prices.get("TLT"),
            "HYG": market_prices.get("HYG"),
        },
        "overview": {
            "total_scored": len(cards),
            "grade_S": sum(1 for c in cards if c["grade"] == "S"),
            "grade_A": sum(1 for c in cards if c["grade"] == "A"),
            "grade_B": sum(1 for c in cards if c["grade"] == "B"),
        },
        "watchlist": cards,
        "top10": cards[:10],
        "themes": [
            {"theme": t, "count": len(v), "symbols": v[:5]}
            for t, v in sorted(theme_counts.items(), key=lambda x: -len(x[1]))
        ],
        "open_signals": open_signals,
        "ai_reviews": [
            {
                "symbol": sym,
                "action": r.get("action"),
                "confidence": r.get("confidence"),
                "reason": r.get("reason"),
            }
            for sym, r in ai_reviews.items()
        ],
    }


def write_dashboard_json(data: dict[str, Any]) -> Path:
    _DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out = _DOCS_DIR / "dashboard_data.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"[Dashboard] Written {out}")
    return out
