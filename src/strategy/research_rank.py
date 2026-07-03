"""Gate + Percentile-Rank research grade (SHADOW MODE, display-only).

Per Codex architecture review (2026-07-02): the official 100-pt additive
score requires 5 independent factors (Technical/Fundamental/Flow/News/
Market) to all be simultaneously high to reach A/S grade, which essentially
never happens for real growth stocks — the ceiling audit confirmed this is
structural, not a bug. Rather than replace the live grade immediately (this
project's established pattern is: compute in shadow, validate with forward
returns, THEN promote — see us_market.py's Minervini/RS work), this module
computes an alternative "research_grade" using the two-stage design Codex
recommended:

1. Gate (pass/fail): data quality, liquidity, not risk-blocked, market
   regime not defensive, and at least one concrete trend signal present.
2. Rank: for gate-passers only, a weighted composite (RS/momentum-heavy,
   matching how this project's own forward-validation data shows RS/
   Minervini picks outperforming) is computed and converted to a
   cross-sectional percentile — so "S" always means "top slice of TODAY's
   universe", not an absolute score nobody can reach.

This is exposed as `strategy.research_rank` per card, alongside (not
replacing) the official `grade`/`action`. It is NOT yet fed into
_log_validation_signals — that should happen once this itself accumulates
enough forward-return history to be trusted, same as every other shadow
signal in this codebase.
"""
from __future__ import annotations

from typing import Any


def _gate(sig: dict[str, Any], regime: dict[str, Any], risk_penalty: int) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not sig.get("liquidity_ok"):
        reasons.append("流動性不足")
    if risk_penalty >= 6:
        reasons.append(f"風險扣分過高({risk_penalty})")
    if regime and regime.get("regime") == "防禦（SPY 跌破 200MA）":
        reasons.append("市場防禦模式")
    has_trend_evidence = bool(
        sig.get("phase2")
        or (sig.get("rs_rating") or 0) >= 70
        or (sig.get("potential") or {}).get("stage") in ("low_base", "early_strength")
    )
    if not has_trend_evidence:
        reasons.append("無明確趨勢證據(RS<70且非phase2且非潛力雷達)")
    return (len(reasons) == 0, reasons)


def _composite(sig: dict[str, Any], score_obj: Any) -> float:
    """Weighted 0-100 composite for ranking purposes only (RS/momentum-heavy,
    per Codex's suggested weighting: RS 45% / trend quality 20% / catalyst
    15% / fundamental 10% / flow+social 10%)."""
    rs = sig.get("rs_rating")
    rs_component = rs if rs is not None else 0

    mt = sig.get("minervini_pass") or 0
    trend_component = mt / 8 * 100

    cc_grade = None
    # catalyst confidence is on the StockScore, not the strategy per_symbol dict
    if score_obj is not None:
        cc = getattr(score_obj, "catalyst_confidence", None) or {}
        cc_grade = cc.get("grade")
    catalyst_map = {"A": 100, "B": 70, "C": 40, "D": 10}
    catalyst_component = catalyst_map.get(cc_grade, 20)

    fundamental_component = 0.0
    flow_social_component = 0.0
    if score_obj is not None:
        fundamental_component = (getattr(score_obj, "fundamental_score", 0) or 0) / 20 * 100
        flow_component = (getattr(score_obj, "flow_score", 0) or 0) / 11 * 100
        social = sig.get("social") or {}
        social_score = social.get("score_0_10")
        social_component = (social_score * 10) if social_score is not None else 50.0
        flow_social_component = (flow_component + social_component) / 2

    return round(
        rs_component * 0.45
        + trend_component * 0.20
        + catalyst_component * 0.15
        + fundamental_component * 0.10
        + flow_social_component * 0.10,
        2,
    )


def build_research_rank(
    per_symbol: dict[str, dict],
    scores_by_symbol: dict[str, Any],
    regime: dict[str, Any],
) -> dict[str, dict]:
    """Returns {symbol: {gate_passed, gate_reasons, composite, percentile,
    research_grade}}. Percentile is computed only across gate-passers so a
    thin gate-passing set doesn't get diluted by symbols that were never
    real candidates."""
    gated: dict[str, float] = {}
    result: dict[str, dict] = {}

    for sym, sig in per_symbol.items():
        score_obj = scores_by_symbol.get(sym)
        risk_penalty = getattr(score_obj, "risk_penalty", 0) if score_obj else 0
        passed, reasons = _gate(sig, regime, risk_penalty)
        composite = _composite(sig, score_obj)
        result[sym] = {
            "gate_passed": passed,
            "gate_reasons": reasons,
            "composite": composite,
            "percentile": None,
            "research_grade": "D",
        }
        if passed:
            gated[sym] = composite

    if gated:
        ranked = sorted(gated.items(), key=lambda kv: kv[1])
        n = len(ranked)
        for rank, (sym, _) in enumerate(ranked):
            pct = (rank + 1) / n * 100
            result[sym]["percentile"] = round(pct, 1)
            if pct >= 95:
                grade = "S"
            elif pct >= 85:
                grade = "A"
            elif pct >= 70:
                grade = "B"
            else:
                grade = "C"
            result[sym]["research_grade"] = grade

    return result
