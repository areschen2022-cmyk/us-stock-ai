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
    # 2026-07-04: no longer a hard gate-fail — the regime-tagged 10y backtest
    # showed individually-strong stocks (RS>=80 + phase2) hold up fine even
    # when SPY is below its 200MA (55.9% alpha win rate, n=1561, the best of
    # the three regimes — see _regime_momentum_multiplier's docstring).
    # Blocking every candidate outright during index weakness was punishing
    # the exact "relative strength survives a weak tape" case this project's
    # own data says is real. Still surfaced as an informational reason (not
    # a pass/fail one) so the UI/knowledge hub can see the regime context.
    has_trend_evidence = bool(
        sig.get("phase2")
        or (sig.get("rs_rating") or 0) >= 70
        or (sig.get("potential") or {}).get("stage") in ("low_base", "early_strength")
    )
    if not has_trend_evidence:
        reasons.append("無明確趨勢證據(RS<70且非phase2且非潛力雷達)")
    return (len(reasons) == 0, reasons)


def _regime_momentum_multiplier(regime: dict[str, Any] | None) -> float:
    """Throttle for the RS/trend (momentum) portion of the composite only.

    2026-07-04: scripts/backtest_shadow_strategy.py was extended to tag each
    historical signal with the ACTUAL regime label at signal time (not just
    calendar year — a 2022-labeled signal could have fired in a brief 2022
    rally). Result contradicted the initial calendar-year-proxy assumption:
    for 'shadow' (RS>=80 + Minervini phase2, i.e. an individually-strong
    stock), alpha win rate in '防禦（SPY 跌破 200MA）' was 55.9% (n=1,561) —
    the BEST of the three regimes, not the worst. A stock that already
    cleared RS>=80 + phase2 is by construction a relative winner, so it
    tends to hold up even while the index is weak. '謹慎（廣度不足）' showed
    69.5% (n=59, too small to trust) for shadow but only 49.3% (n=144) for
    potential_radar's earlier-stage picks — the one regime effect that IS
    directionally consistent and large enough to act on is potential_radar
    weakening when breadth deteriorates, not shadow/RS-driven signals. So
    this multiplier is now a no-op for the RS component; the real modulation
    belongs in _gate (see 2026-07-04 note there) and in the caller's per-group
    interpretation of potential_radar vs shadow, not a blanket rs/trend
    throttle. Kept as an explicit no-op (not deleted) since the pattern
    (extend backtest -> compute real numbers -> revise, not guess) is the
    point of this exercise and next regime findings should slot in here."""
    return 1.0


def _composite(sig: dict[str, Any], score_obj: Any, regime: dict[str, Any] | None = None) -> float:
    """Weighted 0-100 composite for ranking purposes only. Weights: RS 40% /
    trend quality 15% / catalyst 15% / fundamental 10% / flow+social 10% /
    sector strength 10%. Sector strength was added 2026-07-04 (previously
    this module predated src/indicators/sector.py and ran independently of
    it — two shadow signals that should reinforce each other were computed
    in isolation). RS weight trimmed 45%->40% and trend 20%->15% to make
    room without diluting the RS/momentum-dominant design (this project's
    own forward-validation data shows RS/Minervini picks outperforming)."""
    mult = _regime_momentum_multiplier(regime)
    rs = sig.get("rs_rating")
    rs_component = (rs if rs is not None else 0) * mult

    mt = sig.get("minervini_pass") or 0
    trend_component = mt / 8 * 100 * mult

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
        flow_component = (getattr(score_obj, "flow_score", 0) or 0) / 15 * 100
        social = sig.get("social") or {}
        social_score = social.get("score_0_10")
        social_component = (social_score * 10) if social_score is not None else 50.0
        flow_social_component = (flow_component + social_component) / 2

    sector = sig.get("sector") or {}
    sector_score = sector.get("score")
    # 0-20 scale from sector.py -> 0-100; unmapped/unscored sector defaults
    # to neutral (50) rather than 0, so a stock isn't penalized just because
    # its sector ETF failed to fetch that day
    sector_component = (sector_score / 20 * 100) if sector_score is not None else 50.0

    return round(
        rs_component * 0.40
        + trend_component * 0.15
        + catalyst_component * 0.15
        + fundamental_component * 0.10
        + flow_social_component * 0.10
        + sector_component * 0.10,
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
        composite = _composite(sig, score_obj, regime)
        result[sym] = {
            "gate_passed": passed,
            "gate_reasons": reasons,
            "regime_at_signal": (regime or {}).get("regime"),
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
