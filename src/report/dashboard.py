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


def _divergence(cards: list[dict]) -> dict[str, Any]:
    """Quantify disagreement between the live grade engine and the shadow US
    strategy (RS rating). Surfaces cases where the two systems most disagree so
    the validation period is legible at a glance.

    - **missed_strong**: shadow says strong (RS>=80 AND Minervini phase2) but the
      live grade is weak (C/D). These are momentum names the engine may be
      under-rating — the core evidence for eventually re-weighting.
    - **overrated**: live grade is high (S/A) but shadow is weak (RS<50 or not
      phase2). Possible over-rating by the current engine.
    - **avg_gap**: mean |RS_rating - live_score| across comparable stocks.
    """
    gaps: list[float] = []
    missed_strong: list[dict] = []
    overrated: list[dict] = []
    for c in cards:
        st = c.get("strategy") or {}
        rs = st.get("rs_rating")
        if rs is None:
            continue
        gaps.append(abs(rs - c["score"]))
        grade = c["grade"]
        phase2 = bool(st.get("phase2"))
        entry = {
            "symbol": c["symbol"], "grade": grade, "score": c["score"],
            "rs_rating": rs, "minervini_pass": st.get("minervini_pass"),
            "phase2": phase2, "gap": rs - c["score"],
        }
        if rs >= 80 and phase2 and grade in ("C", "D"):
            missed_strong.append(entry)
        elif grade in ("S", "A") and (rs < 50 or not phase2):
            overrated.append(entry)

    missed_strong.sort(key=lambda e: -e["rs_rating"])
    overrated.sort(key=lambda e: e["rs_rating"])
    avg_gap = round(sum(gaps) / len(gaps), 1) if gaps else None
    return {
        "n_compared": len(gaps),
        "avg_gap": avg_gap,
        "missed_strong": missed_strong[:8],
        "overrated": overrated[:8],
        "missed_count": len(missed_strong),
        "overrated_count": len(overrated),
    }


# Self-calibration switches over to z-score once enough trading days exist.
_ZSCORE_MIN_DAYS = 8     # need ~8 prior days for a meaningful std-dev
_ZSCORE_THRESHOLD = 1.5  # today's count must be >=1.5 std above prior mean


def _theme_heating(
    themes_list: list[dict],
    theme_history: dict[str, dict[str, int]] | None,
    today: date,
) -> list[dict]:
    """Mark each theme as rising when today's stock count meaningfully exceeds
    the prior-days baseline. Two modes, auto-selected per data availability:

    - **fixed** (default, < _ZSCORE_MIN_DAYS history): new theme with >=2 today,
      or >=1.8x prior average and >=3 today.
    - **zscore** (>= _ZSCORE_MIN_DAYS history): today's count is >= 1.5 std-devs
      above the prior mean (and >=3 absolute, to avoid noise on tiny baselines).

    Mutates themes_list in place (adds `rising`, `prev_avg`, `zscore`, `mode`)
    and returns the surging subset for the alert section.
    """
    today_str = str(today)
    prior_dates = [d for d in (theme_history or {}) if d != today_str]
    n_prior = len(prior_dates)
    use_zscore = n_prior >= _ZSCORE_MIN_DAYS
    mode = "zscore" if use_zscore else "fixed"
    alerts: list[dict] = []

    for item in themes_list:
        theme = item["theme"]
        today_count = item["count"]
        prior_counts = [theme_history[d].get(theme, 0) for d in prior_dates] if prior_dates else []
        prev_avg = round(sum(prior_counts) / len(prior_counts), 1) if prior_counts else 0.0
        item["prev_avg"] = prev_avg
        item["delta"] = round(today_count - prev_avg, 1)
        item["mode"] = mode

        rising = False
        zscore = None
        if use_zscore and prior_counts:
            mean = sum(prior_counts) / len(prior_counts)
            var = sum((x - mean) ** 2 for x in prior_counts) / len(prior_counts)
            std = var ** 0.5
            if std > 0:
                zscore = round((today_count - mean) / std, 2)
                rising = zscore >= _ZSCORE_THRESHOLD and today_count >= 3
            elif today_count > mean and today_count >= 3:
                # zero-variance baseline that suddenly jumps
                rising = True
        elif prior_dates:
            if prev_avg == 0 and today_count >= 2:
                rising = True
            elif prev_avg > 0 and today_count >= 3 and today_count >= prev_avg * 1.8:
                rising = True

        item["rising"] = rising
        item["zscore"] = zscore
        if rising:
            alerts.append({
                "theme": theme,
                "theme_zh": item["theme_zh"],
                "count": today_count,
                "prev_avg": prev_avg,
                "zscore": zscore,
                "mode": mode,
            })

    return alerts


def _potential_radar(cards: list[dict]) -> list[dict]:
    """潛力雷達：早期觀察名單，跟今日 Top10 可交易清單分開（台股 tw-stock-ai 設計）。
    只收錄非今日 shadow/live_top 名單、且 potential.stage 有值的股票，
    low_base 依收縮品質排序、early_strength 依貼近50MA程度排序。"""
    out = []
    for c in cards:
        pot = (c.get("strategy") or {}).get("potential") or {}
        if not pot.get("stage"):
            continue
        if not (c.get("strategy") or {}).get("liquidity_ok"):
            continue
        out.append({
            "symbol": c["symbol"],
            "name": c.get("name"),
            "price": c.get("price"),
            "score": c.get("score"),
            "stage": pot["stage"],
            "label": pot["label"],
            "reason": pot.get("reason"),
            "contraction_quality": pot.get("contraction_quality"),
            "dist_from_50sma_pct": pot.get("dist_from_50sma_pct"),
            "themes": c.get("themes", []),
        })
    stage_rank = {"early_strength": 0, "low_base": 1, "weakening": 2}
    out.sort(key=lambda x: (stage_rank.get(x["stage"], 9), -(x.get("contraction_quality") or 0)))
    return out


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
        "catalyst_confidence": s.catalyst_confidence,
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
    strategy_signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    strategy_signals = strategy_signals or {}
    strat_per_symbol = strategy_signals.get("per_symbol", {})
    sorted_scores = sorted(scores, key=lambda s: s.total_score, reverse=True)
    cards = [_score_to_card(s) for s in sorted_scores]
    # SHADOW: attach US-strategy signals per card (display-only)
    for c in cards:
        sig = strat_per_symbol.get(c["symbol"])
        if sig:
            c["strategy"] = sig
            if sig.get("rs_rating") is not None:
                c["divergence_gap"] = sig["rs_rating"] - c["score"]

    divergence = _divergence(cards)
    potential_radar = _potential_radar(cards)

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
            # SHADOW: Gate+Percentile research grade (2026-07-02) — separate
            # from the official grade above so an empty A/S band doesn't read
            # as "system broken"; this always has S/A/B by construction
            # (percentile-based) as long as any symbol passes the gate.
            "research_grade_S": sum(1 for c in cards if ((c.get("strategy") or {}).get("research_rank") or {}).get("research_grade") == "S"),
            "research_grade_A": sum(1 for c in cards if ((c.get("strategy") or {}).get("research_rank") or {}).get("research_grade") == "A"),
            "research_grade_B": sum(1 for c in cards if ((c.get("strategy") or {}).get("research_rank") or {}).get("research_grade") == "B"),
            "research_grade_gated_out": sum(1 for c in cards if not ((c.get("strategy") or {}).get("research_rank") or {}).get("gate_passed", True)),
        },
        "highlights": _highlights(sorted_scores, ai_reviews),
        "ai_stats": _ai_stats(
            ai_reviews,
            candidates=sum(1 for c in cards if c["grade"] in ("S", "A")),
        ),
        "data_health": data_health or {},
        "strategy": {  # SHADOW: US-market strategy overlay (display-only)
            "regime": strategy_signals.get("regime", {}),
            "divergence": divergence,
            "sectors": strategy_signals.get("sectors", {}),
            "mode": "shadow",
        },
        "watchlist": cards,
        "top10": cards[:10],
        "potential_radar": potential_radar,
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
