"""US Stock AI — main orchestrator."""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path

from src.config_loader import get_config
from src.data_provider.yfinance_client import (
    fetch_batch_ohlcv,
    fetch_info,
    fetch_market_indices,
    fetch_earnings_calendar,
)
from src.data_provider.sec_client import search_company_cik, get_company_facts, extract_revenue_yoy, fetch_insider_transactions
from src.news.rss_fetcher import fetch_news, fetch_symbol_news
from src.data_provider.social_sentiment import fetch_stocktwits_sentiment
from src.scoring.score_engine import StockScore, compute_score
from src.scoring.grade import grade_label
from src.storage.sqlite_store import SQLiteStore
from src.ai.model_council import ModelCouncil
from src.report.dashboard import build_dashboard_json, write_dashboard_json
from src.report.performance import build_performance_payload, write_performance_json
from src.report.history import update_divergence_history
from src.notifier.telegram import TelegramNotifier
from src.backtest.forward_tracker import fill_open_signals, fill_shadow_signals
from src.strategy import us_market, tw_lessons
from src.strategy.research_rank import build_research_rank
from src.indicators.technical import calc_atr_pct
from src.indicators import sector, market_timing


def _get_revenue_yoy(symbol: str) -> float | None:
    try:
        cik = search_company_cik(symbol)
        if not cik:
            return None
        facts = get_company_facts(cik)
        return extract_revenue_yoy(facts)
    except Exception:
        return None


def _build_shadow_signals(strategy_raw: dict[str, dict], spy_ohlcv) -> dict:
    """Assemble per-symbol US-strategy shadow signals + market regime.
    Two-pass: RS percentile (cross-sectional) → Minervini (uses RS rating)."""
    if not strategy_raw:
        return {"per_symbol": {}, "regime": us_market.market_regime(spy_ohlcv, None)}

    # Pass 1: composite momentum return for cross-sectional RS percentile
    composites: dict[str, float] = {}
    for sym, raw in strategy_raw.items():
        rs = raw.get("rs") or {}
        r6, r12 = rs.get("ret_6m"), rs.get("ret_12m_skip1")
        parts = [v for v in (r6, r12) if v is not None]
        if parts:
            composites[sym] = sum(parts) / len(parts)
    rs_pct = us_market.rs_percentile(composites)

    # Pass 2: Minervini using the RS rating; collect breadth
    per_symbol: dict[str, dict] = {}
    phase2_count = 0
    for sym, raw in strategy_raw.items():
        rating = rs_pct.get(sym)
        ohlcv = raw["ohlcv"]
        mt = us_market.minervini_trend_template(ohlcv, rs_rating=rating)
        if mt.get("phase2"):
            phase2_count += 1
        price = float(ohlcv["Close"].iloc[-1]) if not ohlcv.empty else None
        stop = us_market.conservative_stop(ohlcv, price, calc_atr_pct(ohlcv))
        v2_total, v2_parts = us_market.score_v2(ohlcv, rating)
        ma20_exit = (round(float(ohlcv["Close"].astype(float).tail(20).mean()), 2)
                     if len(ohlcv) >= 20 else None)
        per_symbol[sym] = {
            "score_v2": v2_total,
            "v2_grade": grade_label(v2_total),
            "v2_parts": v2_parts,
            "weekly_up": us_market.weekly_direction_up(ohlcv),
            # MA20 trail exit reference — 10y exit sweep's best protected
            # variant (PF 1.72 vs tight 2ATR stop's 1.58); shown alongside the
            # conservative stop, not replacing it
            "ma20_exit": ma20_exit,
            "rs_rating": rating,
            "rs_score_0_10": (raw.get("rs") or {}).get("rs_score_0_10"),
            "minervini_pass": mt["pass_count"],
            "phase2": mt["phase2"],
            "liquidity_ok": (raw.get("liquidity") or {}).get("passed"),
            "dollar_vol_50d": (raw.get("liquidity") or {}).get("dollar_vol_50d"),
            "entry_price": price,
            "stop_price": stop.get("stop"),
            "entry_quality": (raw.get("tw") or {}).get("entry_quality"),
            "failure_risks": (raw.get("tw") or {}).get("failure_risks", []),
            "social": raw.get("social"),
            "potential": us_market.potential_radar_stage(ohlcv, price, mt.get("phase2", False)),
            "sector": raw.get("sector"),
        }

    breadth_pct = round(phase2_count / len(strategy_raw) * 100, 1) if strategy_raw else None
    regime = us_market.market_regime(spy_ohlcv, breadth_pct)
    return {"per_symbol": per_symbol, "regime": regime}


def _log_ai_review_signals(store, today, ai_reviews: dict, scores, per_symbol: dict,
                           scan_snapshot: dict | None, spy_price: float | None) -> None:
    """Forward-validation for the AI council itself: log every reviewed symbol
    under ai_buy / ai_hold / ai_avoid so the forward tracker scores DeepSeek's
    judgment the same way every other signal group is scored. Scan candidates
    (no StockScore) take their price from the scan snapshot."""
    by_symbol = {s.symbol: s for s in scores}
    scan_prices = {c["symbol"]: c.get("price")
                   for c in (scan_snapshot or {}).get("candidates", [])}
    for grp in ("ai_buy", "ai_hold", "ai_avoid"):
        store.reset_shadow_signals_for_date(today, grp)
    n = 0
    for sym, r in ai_reviews.items():
        action = str(r.get("action") or "").lower()
        if action not in ("buy", "hold", "avoid"):
            continue
        sc = by_symbol.get(sym)
        sig = per_symbol.get(sym, {})
        price = (sc.price if sc else None) or scan_prices.get(sym)
        if not price:
            continue
        store.upsert_shadow_signal(today, f"ai_{action}", {
            "symbol": sym,
            "rs_rating": sig.get("rs_rating"),
            "minervini_pass": sig.get("minervini_pass"),
            "phase2": sig.get("phase2"),
            "live_grade": sc.grade if sc else r.get("grade"),
            "live_score": sc.total_score if sc else r.get("score"),
            "entry_price": price,
            "stop_price": sig.get("stop_price"),
            "spy_entry_price": spy_price,
            "entry_quality": (sig.get("entry_quality") or {}).get("label"),
        })
        n += 1
    print(f"[Main] AI-review shadow signals logged: {n}")


def _entry_quality_map(dash_data: dict) -> dict[str, str]:
    """{symbol: entry_quality_label} from the dashboard cards, for Telegram."""
    out: dict[str, str] = {}
    for c in dash_data.get("watchlist", []):
        eq = (c.get("strategy") or {}).get("entry_quality") or {}
        if eq.get("label"):
            out[c["symbol"]] = eq["label"]
    return out


_SOCIAL_MIN_TAGGED = 5      # need enough sentiment-TAGGED posts (bull+bear) to
                             # trust the ratio — NOT total stream messages,
                             # which StockTwits returns as ~30 regardless of
                             # how many are actually tagged
_SOCIAL_MIN_RATIO = 0.4     # matches social_sentiment._label's "強烈看多" cutoff


def _log_validation_signals(store, today, scores, per_symbol: dict, spy_price: float | None = None) -> None:
    """Record six comparable signal sets for forward-return validation:
    - 'shadow'        : RS>=80 AND Minervini phase2 (what the US strategy would pick)
    - 'live_top'      : top 10 by the current engine's score (what we pick today)
    - 'social_bullish': StockTwits strongly-bullish with enough tagged volume
                        (tests whether retail crowd sentiment has any predictive
                        value, independent of RS/Minervini/current grade)
    - 'confluence'    : in >=2 of the above three groups (tests whether signal
                        agreement across independent methods beats any single
                        signal alone — the basis for a future promote-to-grade
                        decision)
    - 'potential_radar': pre-breakout early-stage candidates (low_base/
                        early_strength) NOT yet in a confirmed phase-2
                        uptrend — tests whether the VCP-style volatility-
                        contraction signal actually precedes good entries
                        over a longer (10-20d) horizon than the other groups
    - 'research_rank' : Gate+Percentile research grade S/A/B (Codex
                        architecture review, 2026-07-02) — tests whether the
                        RS-heavy percentile ranking actually outperforms
                        before it's ever considered for promotion to the
                        official grade
    Not-yet-backfilled rows for today are cleared per group before re-logging,
    so a same-day rerun (e.g. after a filter fix) can't leave stale symbols
    behind — INSERT OR IGNORE alone only adds/skips, it never removes.

    spy_price is stamped as spy_entry_price so forward_tracker can later compute
    alpha (stock return minus SPY return) without an extra fetch — isolates
    stock-picking skill from market beta in the shadow-vs-live comparison."""
    by_symbol = {s.symbol: s for s in scores}

    for grp in ("shadow", "live_top", "social_bullish", "confluence", "potential_radar", "research_rank", "score_v2_sa"):
        store.reset_shadow_signals_for_date(today, grp)

    membership: dict[str, set[str]] = {}

    shadow_n = 0
    for sym, sig in per_symbol.items():
        if (sig.get("rs_rating") or 0) >= 80 and sig.get("phase2") and sig.get("liquidity_ok"):
            sc = by_symbol.get(sym)
            store.upsert_shadow_signal(today, "shadow", {
                "symbol": sym,
                "rs_rating": sig.get("rs_rating"),
                "minervini_pass": sig.get("minervini_pass"),
                "phase2": sig.get("phase2"),
                "live_grade": sc.grade if sc else None,
                "live_score": sc.total_score if sc else None,
                "entry_price": sig.get("entry_price"),
                "stop_price": sig.get("stop_price"),
                "spy_entry_price": spy_price,
                "entry_quality": (sig.get("entry_quality") or {}).get("label"),
            })
            shadow_n += 1
            membership.setdefault(sym, set()).add("shadow")

    live_top = sorted(scores, key=lambda s: s.total_score, reverse=True)[:10]
    for sc in live_top:
        sig = per_symbol.get(sc.symbol, {})
        store.upsert_shadow_signal(today, "live_top", {
            "symbol": sc.symbol,
            "rs_rating": sig.get("rs_rating"),
            "minervini_pass": sig.get("minervini_pass"),
            "phase2": sig.get("phase2"),
            "live_grade": sc.grade,
            "live_score": sc.total_score,
            "entry_price": sc.price,
            "stop_price": sig.get("stop_price"),
            "spy_entry_price": spy_price,
            "entry_quality": (sig.get("entry_quality") or {}).get("label"),
        })
        membership.setdefault(sc.symbol, set()).add("live_top")

    social_n = 0
    for sym, sig in per_symbol.items():
        social = sig.get("social") or {}
        ratio = social.get("sentiment_ratio")
        tagged = social.get("tagged")
        if tagged is None:  # older cached payloads without the field
            tagged = (social.get("bullish") or 0) + (social.get("bearish") or 0)
        if ratio is not None and ratio >= _SOCIAL_MIN_RATIO and tagged >= _SOCIAL_MIN_TAGGED and sig.get("liquidity_ok"):
            sc = by_symbol.get(sym)
            store.upsert_shadow_signal(today, "social_bullish", {
                "symbol": sym,
                "rs_rating": sig.get("rs_rating"),
                "minervini_pass": sig.get("minervini_pass"),
                "phase2": sig.get("phase2"),
                "live_grade": sc.grade if sc else None,
                "live_score": sc.total_score if sc else None,
                "entry_price": sig.get("entry_price") or (sc.price if sc else None),
                "stop_price": sig.get("stop_price"),
                "spy_entry_price": spy_price,
                "entry_quality": (sig.get("entry_quality") or {}).get("label"),
            })
            social_n += 1
            membership.setdefault(sym, set()).add("social_bullish")

    confluence_n = 0
    for sym, grps in membership.items():
        if len(grps) >= 2:
            sig = per_symbol.get(sym, {})
            sc = by_symbol.get(sym)
            store.upsert_shadow_signal(today, "confluence", {
                "symbol": sym,
                "rs_rating": sig.get("rs_rating"),
                "minervini_pass": sig.get("minervini_pass"),
                "phase2": sig.get("phase2"),
                "live_grade": sc.grade if sc else None,
                "live_score": sc.total_score if sc else None,
                "entry_price": sig.get("entry_price") or (sc.price if sc else None),
                "stop_price": sig.get("stop_price"),
                "spy_entry_price": spy_price,
                "entry_quality": (sig.get("entry_quality") or {}).get("label"),
            })
            confluence_n += 1

    potential_n = 0
    for sym, sig in per_symbol.items():
        pot = sig.get("potential") or {}
        if pot.get("stage") in ("low_base", "early_strength") and sig.get("liquidity_ok"):
            sc = by_symbol.get(sym)
            store.upsert_shadow_signal(today, "potential_radar", {
                "symbol": sym,
                "rs_rating": sig.get("rs_rating"),
                "minervini_pass": sig.get("minervini_pass"),
                "phase2": sig.get("phase2"),
                "live_grade": sc.grade if sc else None,
                "live_score": sc.total_score if sc else None,
                "entry_price": sig.get("entry_price") or (sc.price if sc else None),
                "stop_price": sig.get("stop_price"),
                "spy_entry_price": spy_price,
                "entry_quality": (sig.get("entry_quality") or {}).get("label"),
            })
            potential_n += 1

    # score_v2 S/A + weekly-direction group: the exact bucket the 10y backtest
    # validated (bull-regime S alpha20 +3.03%, weekly filter keeps 90.6% of
    # signals) — forward-tracks it live before any promotion decision
    v2_n = 0
    for sym, sig in per_symbol.items():
        if (sig.get("v2_grade") in ("S", "A") and sig.get("weekly_up")
                and sig.get("liquidity_ok")):
            sc = by_symbol.get(sym)
            store.upsert_shadow_signal(today, "score_v2_sa", {
                "symbol": sym,
                "rs_rating": sig.get("rs_rating"),
                "minervini_pass": sig.get("minervini_pass"),
                "phase2": sig.get("phase2"),
                "live_grade": sc.grade if sc else None,
                "live_score": sig.get("score_v2"),
                "entry_price": sig.get("entry_price") or (sc.price if sc else None),
                "stop_price": sig.get("stop_price"),
                "spy_entry_price": spy_price,
                "entry_quality": (sig.get("entry_quality") or {}).get("label"),
            })
            v2_n += 1

    research_n = 0
    for sym, sig in per_symbol.items():
        rr = sig.get("research_rank") or {}
        if rr.get("gate_passed") and rr.get("research_grade") in ("S", "A", "B"):
            sc = by_symbol.get(sym)
            store.upsert_shadow_signal(today, "research_rank", {
                "symbol": sym,
                "rs_rating": sig.get("rs_rating"),
                "minervini_pass": sig.get("minervini_pass"),
                "phase2": sig.get("phase2"),
                "live_grade": sc.grade if sc else None,
                "live_score": sc.total_score if sc else None,
                "entry_price": sig.get("entry_price") or (sc.price if sc else None),
                "stop_price": sig.get("stop_price"),
                "spy_entry_price": spy_price,
                "entry_quality": (sig.get("entry_quality") or {}).get("label"),
            })
            research_n += 1

    print(f"[Validation] logged {shadow_n} shadow + {len(live_top)} live_top + {social_n} social_bullish + {confluence_n} confluence + {potential_n} potential_radar + {research_n} research_rank signals")


def run_daily_update() -> None:
    today = date.today()
    pipeline_start = time.monotonic()
    cfg = get_config()
    store = SQLiteStore()

    print(f"[Main] Starting daily update for {today}")

    # 1. Market context
    market_prices = fetch_market_indices()
    store.save_market_sentiment(today, market_prices, [])

    # 2. Symbols to score
    symbols: list[str] = cfg.get("symbols", [])
    print(f"[Main] Scoring {len(symbols)} symbols")

    # 3. Fetch OHLCV batch (include SPY for relative-strength / regime, and
    #    sector ETFs for sector-strength scoring — neither is in the scored
    #    `symbols` list, so must be requested explicitly)
    fetch_extra = [s for s in (["SPY"] + list(sector.SECTOR_ETFS)) if s not in symbols]
    fetch_list = symbols + fetch_extra
    ohlcv_map = fetch_batch_ohlcv(fetch_list)
    spy_ohlcv = ohlcv_map.get("SPY")
    spy_close_for_sectors = spy_ohlcv["Close"] if (spy_ohlcv is not None and not spy_ohlcv.empty) else None
    sector_scores = sector.build_sector_scores(
        {etf: ohlcv_map.get(etf) for etf in sector.SECTOR_ETFS}, spy_close_for_sectors
    )

    # 4. Fetch news once (shared)
    news_items = fetch_news()

    # 5. Score each symbol
    scores: list[StockScore] = []
    missing_data = 0
    strategy_raw: dict[str, dict] = {}  # SHADOW: per-symbol US-strategy signals
    spy_close = spy_ohlcv["Close"] if (spy_ohlcv is not None and not spy_ohlcv.empty) else None
    for symbol in symbols:
        ohlcv = ohlcv_map.get(symbol)
        if ohlcv is None or ohlcv.empty:
            print(f"[Main] No OHLCV for {symbol}, skipping")
            missing_data += 1
            continue
        try:
            info = fetch_info(symbol)
            rev_yoy = _get_revenue_yoy(symbol)
            sym_news = fetch_symbol_news(symbol)
            earnings_cal = fetch_earnings_calendar(symbol)
            insider_data = fetch_insider_transactions(symbol)
            score = compute_score(
                symbol=symbol,
                ohlcv=ohlcv,
                info=info,
                market_index_prices=market_prices,
                news_items=news_items,
                spy_ohlcv=spy_ohlcv,
                revenue_yoy=rev_yoy,
                insider_data=insider_data,
                earnings_cal=earnings_cal,
                today=today,
                symbol_news=sym_news,
            )
            scores.append(score)
            store.upsert_score(today, score)
            if score.grade in ("S", "A"):
                store.upsert_watch_signal(score, today)

            # SHADOW strategy signals (display-only, does not affect grade yet)
            try:
                rs = us_market.rs_rating_63d(ohlcv["Close"], spy_close) if spy_close is not None else {}
                liq = us_market.liquidity_gate(ohlcv, score.price)
                tw = tw_lessons.entry_quality_from_ohlcv_and_score(ohlcv, score)
                social = fetch_stocktwits_sentiment(symbol)
                sector_etf = sector.map_stock_to_sector_etf(score.sector)
                sector_info = {**sector_scores.get(sector_etf, {}), "sector_etf": sector_etf} if sector_etf else None
                strategy_raw[symbol] = {
                    "ohlcv": ohlcv, "rs": rs, "liquidity": liq, "tw": tw, "social": social,
                    "sector": sector_info,
                }
            except Exception as sx:
                print(f"[Main] shadow-strategy error {symbol}: {sx}")
        except Exception as exc:
            print(f"[Main] Error scoring {symbol}: {exc}")
            traceback.print_exc()

    print(f"[Main] Scored {len(scores)} symbols")

    # 5b. SHADOW: cross-sectional RS percentile + Minervini + market regime
    strategy_signals = _build_shadow_signals(strategy_raw, spy_ohlcv)
    strategy_signals["sectors"] = sector_scores

    # 5b2. RESEARCH RANK: Gate+Percentile grade (Codex architecture review,
    # 2026-07-02) — parallel to the official grade, not replacing it yet
    scores_by_symbol = {s.symbol: s for s in scores}
    research_rank = build_research_rank(
        strategy_signals["per_symbol"], scores_by_symbol, strategy_signals.get("regime", {})
    )
    for sym, rr in research_rank.items():
        if sym in strategy_signals["per_symbol"]:
            strategy_signals["per_symbol"][sym]["research_rank"] = rr

    # 5c. VALIDATION: log shadow picks vs live top picks for forward comparison
    spy_price_today = float(spy_close.iloc[-1]) if spy_close is not None and not spy_close.empty else None
    _log_validation_signals(store, today, scores, strategy_signals["per_symbol"], spy_price_today)

    # 6. Forward return fill-back (live watch signals + shadow validation)
    fill_open_signals(store)
    fill_shadow_signals(store)

    # 7. AI council (token-gated) — coverage driven by the validated v2 S/A +
    # weekly-up research tier (the live >=75 threshold is unreachable under
    # the C-grade ceiling), plus fresh full-market scan candidates on Mondays
    council = ModelCouncil(store=store)
    v2_priority = {sym for sym, sig in strategy_signals["per_symbol"].items()
                   if sig.get("v2_grade") in ("S", "A") and sig.get("weekly_up")}
    candidates = council.select_candidates(scores, priority_symbols=v2_priority)
    ai_reviews = council.review(candidates, today)

    scan_snapshot = None
    try:
        scan_path = Path(__file__).parent / "data" / "market_scan.json"
        if scan_path.exists():
            scan_snapshot = json.loads(scan_path.read_text(encoding="utf-8"))
    except Exception as _scan_e:
        print(f"[Main] scan snapshot read failed (non-fatal): {_scan_e}")
    scan_reviews = council.review_scan_candidates(scan_snapshot, today)
    ai_reviews.update(scan_reviews)
    ai_candidates_count = len(candidates) + len(scan_reviews)
    ai_summaries = council.get_ai_summaries(ai_reviews)

    # 7b. Forward-validate the AI itself (ai_buy/ai_hold/ai_avoid groups)
    _log_ai_review_signals(store, today, ai_reviews, scores,
                           strategy_signals["per_symbol"], scan_snapshot, spy_price_today)

    # 8. Dashboard
    open_signals = store.get_open_signals()
    theme_history = store.get_theme_count_history(today, lookback=4)

    elapsed_min = round((time.monotonic() - pipeline_start) / 60, 1)
    requested = len(symbols)
    success = len(scores)
    miss_rate = (missing_data / requested) if requested else 0.0
    if miss_rate == 0:
        source_status = "正常"
        quality = "高"
    elif miss_rate < 0.15:
        source_status = "正常"
        quality = "中"
    else:
        source_status = "部分缺漏"
        quality = "低"
    data_health = {
        "elapsed_min": elapsed_min,
        "requested": requested,
        "scored": success,
        "missing": missing_data,
        "source_status": source_status,
        "quality": quality,
    }

    dash_data = build_dashboard_json(
        scores, market_prices, open_signals, ai_reviews, today,
        theme_history=theme_history, data_health=data_health,
        strategy_signals=strategy_signals, ai_candidates=ai_candidates_count,
    )
    # Validation: shadow vs live_top forward-return comparison
    dash_data["strategy"]["validation"] = store.get_shadow_performance()

    # Market timing block (IBD distribution days + FTD + VCP-tightness picks),
    # persisted here so the pre-open --telegram-only run can reuse it from the
    # cached dashboard_data.json without refetching anything
    try:
        mt = market_timing.market_timing_summary(spy_df=spy_ohlcv)
        mt["vcp_candidates"] = [
            {"symbol": sym, "label": (sig.get("potential") or {}).get("label"),
             "contraction_ratio": (sig.get("potential") or {}).get("contraction_ratio")}
            for sym, sig in strategy_signals["per_symbol"].items()
            if (sig.get("potential") or {}).get("stage") in ("low_base", "early_strength")
            and (sig.get("rs_rating") or 0) >= 70
        ]
        dash_data["market_timing"] = mt
    except Exception as _mt_e:
        print(f"[Main] market timing failed (non-fatal): {_mt_e}")

    # Knowledge-hub readback (closes the 2026-06-23 loop: write-only until now).
    # data/trading_hub_context.json is refreshed by the local auto_sync run and
    # committed by the watcher, so CI serves the latest local snapshot.
    try:
        hub_path = Path(__file__).parent / "data" / "trading_hub_context.json"
        if hub_path.exists():
            hub = json.loads(hub_path.read_text(encoding="utf-8"))
            rows = [r for r in hub.get("rows", [])
                    if r.get("status") in ("adopted", "backtest_supported")]
            rows.sort(key=lambda r: r.get("confidence") or 0, reverse=True)
            dash_data["hub_context"] = {
                "generated_at": hub.get("generated_at"),
                "points": [{"topic": r.get("topic"), "claim": (r.get("claim") or "")[:160],
                            "status": r.get("status")} for r in rows[:6]],
            }
    except Exception as _hub_e:
        print(f"[Main] hub context readback failed (non-fatal): {_hub_e}")

    # Weekly full-market scan snapshot (watchlist candidates) — refreshed by
    # the Monday CI step, served from whatever the latest snapshot is
    if scan_snapshot:
        dash_data["market_scan"] = scan_snapshot

    write_dashboard_json(dash_data)

    perf_data = build_performance_payload(store, today)
    write_performance_json(perf_data)

    # Append daily divergence snapshot for the trend chart
    update_divergence_history(dash_data["strategy"].get("divergence", {}), today)

    # 9. Telegram morning report (if requested via mode)
    if "--telegram" in sys.argv:
        top = sorted(scores, key=lambda s: s.total_score, reverse=True)[:10]
        overview = {
            **dash_data["overview"],
            "ai_buy": dash_data["ai_stats"]["buy"],
            "ai_hold": dash_data["ai_stats"]["hold"],
            "ai_avoid": dash_data["ai_stats"]["avoid"],
            "ai_total": dash_data["ai_stats"]["total"],
            "ai_candidates": dash_data["ai_stats"]["candidates"],
            "ai_coverage_pct": dash_data["ai_stats"]["coverage_pct"],
            "theme_alerts": dash_data["theme_alerts"],
            "risk_alerts": dash_data["risk_alerts"],
            "data_health": dash_data["data_health"],
            "strategy": dash_data["strategy"],
            "entry_quality_map": _entry_quality_map(dash_data),
        }
        notifier = TelegramNotifier()
        ok = notifier.send_morning_report(top, market_prices, today, ai_summaries, overview)
        status = "ok" if ok else "error"
        store.log_delivery("morning_telegram", status)
        if not ok:
            raise RuntimeError("Telegram morning report failed")

    store.log_delivery("daily_update", "ok", f"scored={len(scores)}")
    print(f"[Main] Done.")


def run_morning_telegram() -> None:
    today = date.today()
    store = SQLiteStore()
    if store.already_delivered_today("morning_telegram"):
        print("[Main] Morning Telegram already sent today — skipping")
        return

    # Morning report runs BEFORE the day's scoring (scoring is an after-close
    # job). So fall back to the most recent scored trading day rather than
    # requiring today's scores — otherwise the report silently never sends.
    scores_raw = store.get_scores_for_date(today)
    data_date = today
    if not scores_raw:
        latest = store.get_latest_scored_date(on_or_before=today)
        if not latest:
            print("[Main] No scores in DB at all — skipping Telegram")
            store.log_delivery("morning_telegram", "skipped", "no scores in db")
            return
        data_date = date.fromisoformat(latest)
        scores_raw = store.get_scores_for_date(data_date)
        print(f"[Main] Using latest scored date {data_date} for morning report")

    # Reconstruct full StockScore objects (incl. warnings) from stored rows
    import json as _json

    def _row_to_score(r: dict) -> StockScore:
        return StockScore(
            symbol=r["symbol"],
            name=r.get("name") or r["symbol"],
            total_score=r["total_score"],
            grade=r["grade"],
            action=r.get("action") or "",
            price=r.get("price"),
            technical_score=r.get("technical_score", 0),
            fundamental_score=r.get("fundamental_score", 0),
            flow_score=r.get("flow_score", 0),
            news_catalyst_score=r.get("news_catalyst_score", 0),
            market_sentiment_score=r.get("market_sentiment_score", 0),
            risk_penalty=r.get("risk_penalty", 0),
            themes=_json.loads(r.get("themes_json") or "[]"),
            warnings=_json.loads(r.get("warnings_json") or "[]"),
            stop_price=None,
            entry_price=r.get("price"),
            atr_pct=r.get("atr_pct", 0),
        )

    all_scores = [_row_to_score(r) for r in scores_raw]
    top = sorted(all_scores, key=lambda s: s.total_score, reverse=True)[:10]

    market_prices = fetch_market_indices()
    theme_history = store.get_theme_count_history(data_date, lookback=4)

    # Reuse the dashboard builder so Telegram and web stay consistent
    dash_data = build_dashboard_json(
        all_scores, market_prices, store.get_open_signals(), {}, data_date,
        theme_history=theme_history,
    )
    # Morning run has no OHLCV → reuse the shadow strategy block + per-card
    # entry-quality persisted by the evening daily-update run.
    strategy_block = dash_data["strategy"]
    eq_map: dict[str, str] = {}
    mt_block = None
    try:
        from pathlib import Path as _Path
        cached = _Path(__file__).parent / "docs" / "dashboard_data.json"
        if cached.exists():
            persisted_full = _json.loads(cached.read_text(encoding="utf-8"))
            persisted = persisted_full.get("strategy")
            if persisted and (persisted.get("divergence", {}) or {}).get("n_compared"):
                strategy_block = persisted
            eq_map = _entry_quality_map(persisted_full)
            mt_block = persisted_full.get("market_timing")
    except Exception as _e:
        print(f"[Main] could not load persisted dashboard: {_e}")

    overview = {
        **dash_data["overview"],
        "ai_buy": dash_data["ai_stats"]["buy"],
        "ai_hold": dash_data["ai_stats"]["hold"],
        "ai_avoid": dash_data["ai_stats"]["avoid"],
        "ai_total": dash_data["ai_stats"]["total"],
        "ai_candidates": dash_data["ai_stats"]["candidates"],
        "ai_coverage_pct": dash_data["ai_stats"]["coverage_pct"],
        "theme_alerts": dash_data["theme_alerts"],
        "risk_alerts": dash_data["risk_alerts"],
        "data_health": {"source_status": "讀取快取", "quality": "高"},
        "strategy": strategy_block,
        "entry_quality_map": eq_map or _entry_quality_map(dash_data),
        "market_timing": mt_block,
    }
    overview["data_date"] = str(data_date)
    notifier = TelegramNotifier()
    ok = notifier.send_morning_report(top, market_prices, today, overview=overview)
    store.log_delivery("morning_telegram", "ok" if ok else "error", f"data_date={data_date}")
    if not ok:
        raise RuntimeError("Telegram morning report failed")


if __name__ == "__main__":
    if "--telegram-only" in sys.argv:
        run_morning_telegram()
    else:
        run_daily_update()
