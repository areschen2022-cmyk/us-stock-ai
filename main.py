"""US Stock AI — main orchestrator."""
from __future__ import annotations

import sys
import time
import traceback
from datetime import date

from src.config_loader import get_config
from src.data_provider.yfinance_client import (
    fetch_batch_ohlcv,
    fetch_info,
    fetch_market_indices,
)
from src.data_provider.sec_client import search_company_cik, get_company_facts, extract_revenue_yoy
from src.news.rss_fetcher import fetch_news
from src.scoring.score_engine import StockScore, compute_score
from src.storage.sqlite_store import SQLiteStore
from src.ai.model_council import ModelCouncil
from src.report.dashboard import build_dashboard_json, write_dashboard_json
from src.report.performance import build_performance_payload, write_performance_json
from src.report.history import update_divergence_history
from src.notifier.telegram import TelegramNotifier
from src.backtest.forward_tracker import fill_open_signals, fill_shadow_signals
from src.strategy import us_market
from src.indicators.technical import calc_atr_pct


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
        per_symbol[sym] = {
            "rs_rating": rating,
            "rs_score_0_10": (raw.get("rs") or {}).get("rs_score_0_10"),
            "minervini_pass": mt["pass_count"],
            "phase2": mt["phase2"],
            "liquidity_ok": (raw.get("liquidity") or {}).get("passed"),
            "dollar_vol_50d": (raw.get("liquidity") or {}).get("dollar_vol_50d"),
            "entry_price": price,
            "stop_price": stop.get("stop"),
        }

    breadth_pct = round(phase2_count / len(strategy_raw) * 100, 1) if strategy_raw else None
    regime = us_market.market_regime(spy_ohlcv, breadth_pct)
    return {"per_symbol": per_symbol, "regime": regime}


def _log_validation_signals(store, today, scores, per_symbol: dict) -> None:
    """Record two comparable signal sets for forward-return validation:
    - 'shadow'   : RS>=80 AND Minervini phase2 (what the US strategy would pick)
    - 'live_top' : top 10 by the current engine's score (what we pick today)
    INSERT OR IGNORE makes this idempotent per (date, symbol, group)."""
    by_symbol = {s.symbol: s for s in scores}

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
            })
            shadow_n += 1

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
        })
    print(f"[Validation] logged {shadow_n} shadow + {len(live_top)} live_top signals")


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

    # 3. Fetch OHLCV batch (include SPY for relative-strength / regime — it is
    #    not in the scored `symbols` list, so must be requested explicitly)
    fetch_list = symbols + (["SPY"] if "SPY" not in symbols else [])
    ohlcv_map = fetch_batch_ohlcv(fetch_list)
    spy_ohlcv = ohlcv_map.get("SPY")

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
            score = compute_score(
                symbol=symbol,
                ohlcv=ohlcv,
                info=info,
                market_index_prices=market_prices,
                news_items=news_items,
                spy_ohlcv=spy_ohlcv,
                revenue_yoy=rev_yoy,
                today=today,
            )
            scores.append(score)
            store.upsert_score(today, score)
            if score.grade in ("S", "A"):
                store.upsert_watch_signal(score, today)

            # SHADOW strategy signals (display-only, does not affect grade yet)
            try:
                rs = us_market.rs_rating_63d(ohlcv["Close"], spy_close) if spy_close is not None else {}
                liq = us_market.liquidity_gate(ohlcv, score.price)
                strategy_raw[symbol] = {"ohlcv": ohlcv, "rs": rs, "liquidity": liq}
            except Exception as sx:
                print(f"[Main] shadow-strategy error {symbol}: {sx}")
        except Exception as exc:
            print(f"[Main] Error scoring {symbol}: {exc}")
            traceback.print_exc()

    print(f"[Main] Scored {len(scores)} symbols")

    # 5b. SHADOW: cross-sectional RS percentile + Minervini + market regime
    strategy_signals = _build_shadow_signals(strategy_raw, spy_ohlcv)

    # 5c. VALIDATION: log shadow picks vs live top picks for forward comparison
    _log_validation_signals(store, today, scores, strategy_signals["per_symbol"])

    # 6. Forward return fill-back (live watch signals + shadow validation)
    fill_open_signals(store)
    fill_shadow_signals(store)

    # 7. AI council (token-gated)
    council = ModelCouncil(store=store)
    candidates = council.select_candidates(scores)
    ai_reviews = council.review(candidates, today)
    ai_summaries = council.get_ai_summaries(ai_reviews)

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
        strategy_signals=strategy_signals,
    )
    # Validation: shadow vs live_top forward-return comparison
    dash_data["strategy"]["validation"] = store.get_shadow_performance()
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
    # Morning run has no OHLCV → reuse the shadow strategy block persisted by the
    # evening daily-update run (regime/divergence don't change overnight).
    strategy_block = dash_data["strategy"]
    try:
        from pathlib import Path as _Path
        cached = _Path(__file__).parent / "docs" / "dashboard_data.json"
        if cached.exists():
            persisted = _json.loads(cached.read_text(encoding="utf-8")).get("strategy")
            if persisted and (persisted.get("divergence", {}) or {}).get("n_compared"):
                strategy_block = persisted
    except Exception as _e:
        print(f"[Main] could not load persisted strategy block: {_e}")

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
