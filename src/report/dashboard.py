"""Build dashboard JSON payload from today's scores."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from src.scoring.score_engine import StockScore

_DOCS_DIR = Path(__file__).parent.parent.parent / "docs"

THEME_ZH = {
    "ai_infra": "AI 基礎建設",
    "semiconductor": "半導體",
    "cloud_saas": "雲端軟體",
    "cybersecurity": "資安",
    "defense": "國防軍工",
    "energy_power": "能源電力",
    "crypto_fintech": "加密金融",
    "glp1_biotech": "GLP-1 生技",
    "emerging_market": "新興市場",
}

AI_ACTION_ZH = {"Buy": "同意", "Hold": "保留", "Avoid": "不建議"}


def _market_direction(vix: float) -> str:
    if vix < 18:
        return "多頭"
    elif vix < 25:
        return "中性"
    return "空頭"


def _ai_stats(ai_reviews: dict[str, dict], candidates: int = 0) -> dict[str, int]:
    buy = sum(1 for r in ai_reviews.values() if r.get("action") == "Buy")
    hold = sum(1 for r in ai_reviews.values() if r.get("action") == "Hold")
    avoid = sum(1 for r in ai_reviews.values() if r.get("action") == "Avoid")
    total = len(ai_reviews)
    # 複核覆蓋率 = 已複核 / 應複核候選（S+A 級）。candidates=0 時回傳 None 避免除以零
    coverage = round(total / candidates * 100) if candidates else None
    return {
        "buy": buy, "hold": hold, "avoid": avoid, "total": total,
        "candidates": candidates, "coverage_pct": coverage,
    }


def _theme_heating(
    themes_list: list[dict],
    theme_history: dict[str, dict[str, int]] | None,
    today: date,
) -> list[dict]:
    """Mark each theme as rising when today's stock count meaningfully exceeds the
    prior-days average. Mutates themes_list in place (adds `rising`, `prev_avg`,
    `delta`) and returns the subset of surging themes for the alert section."""
    today_str = str(today)
    # Prior days = all history dates except today
    prior_dates = [d for d in (theme_history or {}) if d != today_str]
    alerts: list[dict] = []

    for item in themes_list:
        theme = item["theme"]
        today_count = item["count"]
        prior_counts = [theme_history[d].get(theme, 0) for d in prior_dates] if prior_dates else []
        prev_avg = round(sum(prior_counts) / len(prior_counts), 1) if prior_counts else 0.0
        item["prev_avg"] = prev_avg
        item["delta"] = round(today_count - prev_avg, 1)

        # Rising: new theme (no prior presence) with >=2 today, or >=1.8x average and >=3
        rising = False
        if prior_dates:
            if prev_avg == 0 and today_count >= 2:
                rising = True
            elif prev_avg > 0 and today_count >= 3 and today_count >= prev_avg * 1.8:
                rising = True
        item["rising"] = rising
        if rising:
            alerts.append({
                "theme": theme,
                "theme_zh": item["theme_zh"],
                "count": today_count,
                "prev_avg": prev_avg,
            })

    return alerts


def _highlights(sorted_scores: list[StockScore], ai_reviews: dict[str, dict]) -> list[dict]:
    top = [s for s in sorted_scores if s.grade in ("S", "A")][:5]
    result = []
    for s in top:
        ai = ai_reviews.get(s.symbol, {})
        ai_action = ai.get("action")
        result.append({
            "symbol": s.symbol,
            "score": s.total_score,
            "grade": s.grade,
            "action": s.action,
            "ai_action": ai_action,
            "ai_action_zh": AI_ACTION_ZH.get(ai_action, "未複核"),
            "price": s.price,
        })
    return result


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
        "risk_penalty": s.risk_penalty,
    }


def build_dashboard_json(
    scores: list[StockScore],
    market_prices: dict[str, float],
    open_signals: list[dict],
    ai_reviews: dict[str, dict],
    today: date | None = None,
    theme_history: dict[str, dict[str, int]] | None = None,
    data_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    sorted_scores = sorted(scores, key=lambda s: s.total_score, reverse=True)
    cards = [_score_to_card(s) for s in sorted_scores]

    vix = market_prices.get("^VIX") or 20.0

    # Theme aggregation with ZH labels
    theme_counts: dict[str, list[dict]] = {}
    for s in sorted_scores:
        for t in s.themes:
            theme_counts.setdefault(t, []).append(
                {"symbol": s.symbol, "score": s.total_score}
            )

    themes_list = [
        {
            "theme": t,
            "theme_zh": THEME_ZH.get(t, t),
            "count": len(v),
            "symbols": v[:5],
        }
        for t, v in sorted(theme_counts.items(), key=lambda x: -len(x[1]))
    ]

    # Theme heating detection (today vs prior-days average)
    theme_alerts = _theme_heating(themes_list, theme_history, today)

    # Risk alerts: high risk penalty or grade D
    risk_alerts = [
        _score_to_card(s)
        for s in sorted_scores
        if s.risk_penalty >= 6 or (s.grade == "D" and s.total_score > 30)
    ][:5]

    return {
        "generated_at": str(today),
        "market": {
            "SPY": market_prices.get("SPY"),
            "QQQ": market_prices.get("QQQ"),
            "VIX": vix,
            "TLT": market_prices.get("TLT"),
            "HYG": market_prices.get("HYG"),
            "IWM": market_prices.get("IWM"),
            "SMH": market_prices.get("SMH"),
        },
        "market_direction": _market_direction(vix),
        "overview": {
            "total_scored": len(cards),
            "grade_S": sum(1 for c in cards if c["grade"] == "S"),
            "grade_A": sum(1 for c in cards if c["grade"] == "A"),
            "grade_B": sum(1 for c in cards if c["grade"] == "B"),
            "grade_C": sum(1 for c in cards if c["grade"] == "C"),
            "grade_D": sum(1 for c in cards if c["grade"] == "D"),
        },
        "highlights": _highlights(sorted_scores, ai_reviews),
        "ai_stats": _ai_stats(
            ai_reviews,
            candidates=sum(1 for c in cards if c["grade"] in ("S", "A")),
        ),
        "data_health": data_health or {},
        "watchlist": cards,
        "top10": cards[:10],
        "themes": themes_list,
        "theme_alerts": theme_alerts,
        "risk_alerts": risk_alerts,
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
