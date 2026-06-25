"""SQLite persistence for US Stock AI."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.scoring.score_engine import StockScore

_DEFAULT_DB = Path(__file__).parent.parent.parent / "data" / "us_stock_ai.sqlite3"


class SQLiteStore:
    def __init__(self, path: Path = _DEFAULT_DB) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS daily_scores (
                    as_of_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    total_score INTEGER NOT NULL,
                    grade TEXT NOT NULL,
                    action TEXT,
                    price REAL,
                    technical_score INTEGER DEFAULT 0,
                    fundamental_score INTEGER DEFAULT 0,
                    flow_score INTEGER DEFAULT 0,
                    news_catalyst_score INTEGER DEFAULT 0,
                    market_sentiment_score INTEGER DEFAULT 0,
                    risk_penalty INTEGER DEFAULT 0,
                    themes_json TEXT DEFAULT '[]',
                    reasons_json TEXT DEFAULT '{}',
                    warnings_json TEXT DEFAULT '[]',
                    matched_headlines_json TEXT DEFAULT '[]',
                    atr_pct REAL DEFAULT 0,
                    sector TEXT,
                    industry TEXT,
                    market_cap REAL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (as_of_date, symbol)
                );

                CREATE TABLE IF NOT EXISTS watch_signals (
                    signal_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    score INTEGER NOT NULL,
                    grade TEXT NOT NULL,
                    action TEXT,
                    entry_price REAL,
                    stop_price REAL,
                    themes_json TEXT DEFAULT '[]',
                    return_3d REAL,
                    return_5d REAL,
                    return_10d REAL,
                    return_20d REAL,
                    entry_triggered INTEGER DEFAULT 0,
                    stop_hit INTEGER DEFAULT 0,
                    outcome TEXT,
                    PRIMARY KEY (signal_date, symbol)
                );

                CREATE TABLE IF NOT EXISTS ai_council_reviews (
                    review_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    score INTEGER,
                    grade TEXT,
                    consensus_action TEXT,
                    confidence REAL,
                    reason TEXT,
                    model_reviews_json TEXT DEFAULT '[]',
                    token_budget_used INTEGER DEFAULT 0,
                    PRIMARY KEY (review_date, symbol)
                );

                CREATE TABLE IF NOT EXISTS market_sentiment (
                    as_of_date TEXT PRIMARY KEY,
                    spy_price REAL,
                    qqq_price REAL,
                    vix_level REAL,
                    tlt_price REAL,
                    hyg_price REAL,
                    sector_json TEXT DEFAULT '{}',
                    market_reasons_json TEXT DEFAULT '[]'
                );

                CREATE TABLE IF NOT EXISTS news_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_date TEXT NOT NULL,
                    symbol TEXT,
                    source TEXT,
                    title TEXT NOT NULL,
                    url TEXT,
                    sentiment REAL DEFAULT 0,
                    relevance_score REAL DEFAULT 0,
                    fetched_at TEXT
                );

                CREATE TABLE IF NOT EXISTS delivery_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    delivered_at TEXT NOT NULL,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT
                );

                CREATE TABLE IF NOT EXISTS data_retry_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    queued_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    symbol TEXT,
                    reason TEXT,
                    attempts INTEGER DEFAULT 0,
                    resolved INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS shadow_signals (
                    signal_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    grp TEXT NOT NULL,              -- 'shadow' | 'live_top'
                    rs_rating INTEGER,
                    minervini_pass INTEGER,
                    phase2 INTEGER DEFAULT 0,
                    live_grade TEXT,
                    live_score INTEGER,
                    entry_price REAL,
                    stop_price REAL,
                    return_3d REAL,
                    return_5d REAL,
                    return_10d REAL,
                    return_20d REAL,
                    outcome TEXT,
                    PRIMARY KEY (signal_date, symbol, grp)
                );

                CREATE TABLE IF NOT EXISTS knowledge_exports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exported_at TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    domain TEXT DEFAULT 'us_stock',
                    status TEXT DEFAULT 'draft',
                    knowledge_hub_id TEXT,
                    signal_date TEXT
                );
                """
            )

    # ── daily scores ──────────────────────────────────────────────────────────

    def upsert_score(self, as_of_date: date, score: StockScore) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO daily_scores
                   (as_of_date, symbol, name, total_score, grade, action, price,
                    technical_score, fundamental_score, flow_score,
                    news_catalyst_score, market_sentiment_score, risk_penalty,
                    themes_json, reasons_json, warnings_json, matched_headlines_json,
                    atr_pct, sector, industry, market_cap)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(as_of_date),
                    score.symbol,
                    score.name,
                    score.total_score,
                    score.grade,
                    score.action,
                    score.price,
                    score.technical_score,
                    score.fundamental_score,
                    score.flow_score,
                    score.news_catalyst_score,
                    score.market_sentiment_score,
                    score.risk_penalty,
                    json.dumps(score.themes),
                    json.dumps(score.reasons),
                    json.dumps(score.warnings),
                    json.dumps(score.matched_headlines),
                    score.atr_pct,
                    score.sector,
                    score.industry,
                    score.market_cap,
                ),
            )

    def get_scores_for_date(self, as_of_date: date) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_scores WHERE as_of_date=? ORDER BY total_score DESC",
                (str(as_of_date),),
            ).fetchall()
            cols = [d[0] for d in conn.execute("SELECT * FROM daily_scores LIMIT 0").description]
        # rebuild with column names
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM daily_scores WHERE as_of_date=? ORDER BY total_score DESC",
                (str(as_of_date),),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_latest_scored_date(self, on_or_before: date | None = None) -> str | None:
        """Most recent as_of_date that has scores (optionally on/before a date).
        Morning reports run before the day's scoring, so they must fall back to
        the latest available trading day rather than strictly today."""
        with self._connect() as conn:
            if on_or_before is not None:
                row = conn.execute(
                    "SELECT MAX(as_of_date) FROM daily_scores WHERE as_of_date <= ?",
                    (str(on_or_before),),
                ).fetchone()
            else:
                row = conn.execute("SELECT MAX(as_of_date) FROM daily_scores").fetchone()
        return row[0] if row and row[0] else None

    def get_theme_count_history(self, as_of: date, lookback: int = 4) -> dict[str, dict[str, int]]:
        """Return {date_str: {theme: stock_count}} for the most recent `lookback`
        trading days up to and including as_of. Used for theme-heating detection."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            dates = conn.execute(
                """SELECT DISTINCT as_of_date FROM daily_scores
                   WHERE as_of_date <= ? ORDER BY as_of_date DESC LIMIT ?""",
                (str(as_of), lookback),
            ).fetchall()
            date_list = [r["as_of_date"] for r in dates]
            history: dict[str, dict[str, int]] = {}
            for d in date_list:
                rows = conn.execute(
                    "SELECT themes_json FROM daily_scores WHERE as_of_date=?",
                    (d,),
                ).fetchall()
                counts: dict[str, int] = {}
                for r in rows:
                    try:
                        themes = json.loads(r["themes_json"] or "[]")
                    except Exception:
                        themes = []
                    for t in themes:
                        counts[t] = counts.get(t, 0) + 1
                history[d] = counts
            return history

    # ── watch signals ─────────────────────────────────────────────────────────

    def upsert_watch_signal(self, score: StockScore, signal_date: date) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO watch_signals
                   (signal_date, symbol, name, score, grade, action, entry_price, stop_price, themes_json)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    str(signal_date),
                    score.symbol,
                    score.name,
                    score.total_score,
                    score.grade,
                    score.action,
                    score.entry_price,
                    score.stop_price,
                    json.dumps(score.themes),
                ),
            )

    def get_open_signals(self) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM watch_signals WHERE outcome IS NULL ORDER BY signal_date DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── AI council ────────────────────────────────────────────────────────────

    def save_ai_review(self, review_date: date, symbol: str, review: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO ai_council_reviews
                   (review_date, symbol, score, grade, consensus_action, confidence, reason, model_reviews_json, token_budget_used)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    str(review_date),
                    symbol,
                    review.get("score"),
                    review.get("grade"),
                    review.get("action"),
                    review.get("confidence"),
                    review.get("reason"),
                    json.dumps(review.get("model_reviews", [])),
                    review.get("tokens_used", 0),
                ),
            )

    # ── market sentiment ──────────────────────────────────────────────────────

    def save_market_sentiment(self, as_of_date: date, prices: dict, reasons: list[str]) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO market_sentiment
                   (as_of_date, spy_price, qqq_price, vix_level, tlt_price, hyg_price, market_reasons_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    str(as_of_date),
                    prices.get("SPY"),
                    prices.get("QQQ"),
                    prices.get("^VIX"),
                    prices.get("TLT"),
                    prices.get("HYG"),
                    json.dumps(reasons),
                ),
            )

    # ── delivery log ──────────────────────────────────────────────────────────

    def log_delivery(self, task: str, status: str, detail: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO delivery_log (delivered_at, task, status, detail) VALUES (?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), task, status, detail),
            )

    def already_delivered_today(self, task: str) -> bool:
        today = str(date.today())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM delivery_log WHERE task=? AND delivered_at LIKE ? AND status='ok' LIMIT 1",
                (task, f"{today}%"),
            ).fetchone()
        return row is not None

    # ── knowledge export tracking ─────────────────────────────────────────────

    def save_knowledge_export(self, topic: str, claim: str, signal_date: str, hub_id: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO knowledge_exports (exported_at, topic, claim, signal_date, knowledge_hub_id)
                   VALUES (?,?,?,?,?)""",
                (datetime.now(timezone.utc).isoformat(), topic, claim, signal_date, hub_id),
            )

    def get_unexported_outcomes(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT ws.signal_date, ws.symbol, ws.score, ws.grade, ws.outcome,
                          ws.return_5d, ws.return_10d
                   FROM watch_signals ws
                   LEFT JOIN knowledge_exports ke ON ke.signal_date = ws.signal_date AND ke.topic LIKE '%'||ws.symbol||'%'
                   WHERE ws.outcome IS NOT NULL AND ke.id IS NULL
                   ORDER BY ws.signal_date DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
