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
from src.notifier.telegram import TelegramNotifier
from src.backtest.forward_tracker import fill_open_signals


def _get_revenue_yoy(symbol: str) -> float | None:
    try:
        cik = search_company_cik(symbol)
        if not cik:
            return None
        facts = get_company_facts(cik)
        return extract_revenue_yoy(facts)
    except Exception:
        return None


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

    # 3. Fetch OHLCV batch
    ohlcv_map = fetch_batch_ohlcv(symbols)
    spy_ohlcv = ohlcv_map.get("SPY")

    # 4. Fetch news once (shared)
    news_items = fetch_news()

    # 5. Score each symbol
    scores: list[StockScore] = []
    missing_data = 0
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
        except Exception as exc:
            print(f"[Main] Error scoring {symbol}: {exc}")
            traceback.print_exc()

    print(f"[Main] Scored {len(scores)} symbols")

    # 6. Forward return fill-back
    fill_open_signals(store)

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
    )
    write_dashboard_json(dash_data)

    perf_data = build_performance_payload(store, today)
    write_performance_json(perf_data)

    # 9. Telegram morning report (if requested via mode)
    if "--telegram" in sys.argv:
        top = sorted(scores, key=lambda s: s.total_score, reverse=True)[:10]
        overview = {
            **dash_data["overview"],
            "ai_buy": dash_data["ai_stats"]["buy"],
            "ai_hold": dash_data["ai_stats"]["hold"],
            "ai_avoid": dash_data["ai_stats"]["avoid"],
            "ai_total": dash_data["ai_stats"]["total"],
            "theme_alerts": dash_data["theme_alerts"],
            "risk_alerts": dash_data["risk_alerts"],
            "data_health": dash_data["data_health"],
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

    scores_raw = store.get_scores_for_date(today)
    if not scores_raw:
        print("[Main] No scores for today yet — skipping Telegram")
        return

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
    theme_history = store.get_theme_count_history(today, lookback=4)

    # Reuse the dashboard builder so Telegram and web stay consistent
    dash_data = build_dashboard_json(
        all_scores, market_prices, store.get_open_signals(), {}, today,
        theme_history=theme_history,
    )
    overview = {
        **dash_data["overview"],
        "ai_buy": dash_data["ai_stats"]["buy"],
        "ai_hold": dash_data["ai_stats"]["hold"],
        "ai_avoid": dash_data["ai_stats"]["avoid"],
        "ai_total": dash_data["ai_stats"]["total"],
        "theme_alerts": dash_data["theme_alerts"],
        "risk_alerts": dash_data["risk_alerts"],
        "data_health": {"source_status": "讀取快取", "quality": "高"},
    }
    notifier = TelegramNotifier()
    ok = notifier.send_morning_report(top, market_prices, today, overview=overview)
    store.log_delivery("morning_telegram", "ok" if ok else "error")
    if not ok:
        raise RuntimeError("Telegram morning report failed")


if __name__ == "__main__":
    if "--telegram-only" in sys.argv:
        run_morning_telegram()
    else:
        run_daily_update()
