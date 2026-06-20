from __future__ import annotations

import json
from datetime import date

from src.scoring.score_engine import StockScore
from src.storage.sqlite_store import SQLiteStore


def _score(symbol: str, total: int) -> StockScore:
    return StockScore(
        symbol=symbol,
        name=f"{symbol} Inc.",
        total_score=total,
        grade="A",
        action="Watch / Buy Pullback",
        price=123.45,
        technical_score=25,
        fundamental_score=15,
        flow_score=10,
        news_catalyst_score=8,
        market_sentiment_score=7,
        risk_penalty=3,
        themes=["ai_infra"],
        reasons={"technical": ["Above 50MA"]},
        warnings=["ATR 3.2%"],
        matched_headlines=["Test headline"],
        atr_pct=3.2,
        sector="Technology",
        industry="Software",
        market_cap=1_000_000_000,
        entry_price=123.45,
        stop_price=115.0,
    )


def test_upsert_score_and_get_scores_for_date(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "test.sqlite3")
    run_date = date(2026, 6, 20)

    store.upsert_score(run_date, _score("NVDA", 80))
    store.upsert_score(run_date, _score("AAPL", 70))
    rows = store.get_scores_for_date(run_date)

    assert [row["symbol"] for row in rows] == ["NVDA", "AAPL"]
    assert rows[0]["total_score"] == 80
    assert json.loads(rows[0]["themes_json"]) == ["ai_infra"]
    assert json.loads(rows[0]["reasons_json"])["technical"] == ["Above 50MA"]


def test_upsert_score_replaces_existing_row(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "test.sqlite3")
    run_date = date(2026, 6, 20)

    store.upsert_score(run_date, _score("NVDA", 70))
    store.upsert_score(run_date, _score("NVDA", 88))
    rows = store.get_scores_for_date(run_date)

    assert len(rows) == 1
    assert rows[0]["total_score"] == 88
