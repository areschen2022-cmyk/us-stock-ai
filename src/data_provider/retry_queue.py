"""Simple retry queue for failed data fetches — stored in SQLite."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_DB = Path(__file__).parent.parent.parent / "data" / "us_stock_ai.sqlite3"


class RetryQueue:
    def __init__(self, db_path: Path = _DB) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS data_retry_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    queued_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    symbol TEXT,
                    reason TEXT,
                    attempts INTEGER DEFAULT 0,
                    resolved INTEGER DEFAULT 0
                )"""
            )

    def enqueue(self, provider: str, symbol: str, reason: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO data_retry_queue (queued_at, provider, symbol, reason) VALUES (?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), provider, symbol, reason),
            )

    def pending(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, provider, symbol, reason, attempts FROM data_retry_queue WHERE resolved=0 ORDER BY id"
            ).fetchall()
        return [{"id": r[0], "provider": r[1], "symbol": r[2], "reason": r[3], "attempts": r[4]} for r in rows]

    def resolve(self, row_id: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE data_retry_queue SET resolved=1 WHERE id=?", (row_id,))
