"""A group's own selection score must be persisted with its signal.

2026-07-31 research_rank post-mortem: the group grades purely by cross-sectional
percentile with no absolute floor, so "B" means "top 30% of whoever passed the
gate today" however weak that field is. Testing whether a floor would have
helped was impossible — the composite was computed, used to grade, and thrown
away. Persisting it turns that calibration into a query.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.sqlite_store import SQLiteStore


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(path=tmp_path / "t.sqlite3")


def _signal(**over):
    base = {
        "symbol": "AAA", "rs_rating": 88, "minervini_pass": 7, "phase2": True,
        "live_grade": "C", "live_score": 60, "entry_price": 10.0,
        "stop_price": 9.0, "spy_entry_price": 700.0, "entry_quality": "可進場",
        "selector_score": 78.87, "selector_percentile": 100.0,
    }
    base.update(over)
    return base


def _read(store, col):
    with store._connect() as conn:
        return conn.execute(f"SELECT {col} FROM shadow_signals").fetchone()


def test_selector_score_round_trips(store):
    store.upsert_shadow_signal(date(2026, 7, 31), "research_rank", _signal())
    assert _read(store, "selector_score, selector_percentile") == (78.87, 100.0)


def test_absent_selector_score_is_null_not_zero(store):
    """Groups with no selector score of their own must store NULL — a 0.0 would
    look like a real bottom-ranked score to any later calibration query."""
    sig = _signal()
    del sig["selector_score"], sig["selector_percentile"]
    store.upsert_shadow_signal(date(2026, 7, 31), "live_top", sig)
    assert _read(store, "selector_score, selector_percentile") == (None, None)


def test_migration_adds_columns_to_a_preexisting_table(tmp_path):
    """The column must appear on databases created before this change, not just
    on fresh ones — the production DB is committed and long-lived."""
    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE shadow_signals (signal_date TEXT, symbol TEXT, grp TEXT,"
        " rs_rating REAL, minervini_pass INT, phase2 INT, live_grade TEXT,"
        " live_score REAL, entry_price REAL, stop_price REAL,"
        " PRIMARY KEY (signal_date, symbol, grp))"
    )
    conn.commit()
    conn.close()

    SQLiteStore(path=db)  # runs the migration

    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(shadow_signals)")}
    assert {"selector_score", "selector_percentile"} <= cols
