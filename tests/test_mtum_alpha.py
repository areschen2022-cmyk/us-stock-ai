"""Momentum-factor alpha is a persisted column, not a report-time computation.

2026-07 lesson: every group's SPY-alpha looked catastrophic while several were
beating their own factor — MTUM lost 8.7pp to SPY that month. Judging a
momentum system against SPY alone conflates "our picks are bad" with "the
factor drew down", so the pair must always be available together.

Persisted rather than recomputed so the benchmark entry price is the one from
signal time; a later refetch would silently re-baseline settled history.
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


def test_mtum_columns_exist(store):
    with store._connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(shadow_signals)")}
    assert {"mtum_entry_price", "mtum_return_5d", "mtum_return_10d",
            "alpha_mtum_5d", "alpha_mtum_10d"} <= cols


def test_row_stays_open_until_mtum_alpha_is_filled(store):
    """The backfill query drives which rows get worked on. If a fully-settled
    row stopped qualifying before the MTUM columns existed, they could never
    populate — the same trap that left return_20d NULL on 346 rows."""
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO shadow_signals (signal_date, symbol, grp, entry_price,"
            " return_3d, return_5d, return_10d, return_20d, alpha_5d, alpha_10d,"
            " outcome, stop_hit_20d) VALUES"
            " ('2026-07-01','AAA','live_top',10.0,1,1,1,1,1,1,'win',0)"
        )

    assert [r["symbol"] for r in store.get_open_shadow_signals()] == ["AAA"]

    with store._connect() as conn:
        conn.execute("UPDATE shadow_signals SET alpha_mtum_5d=1, alpha_mtum_10d=1")

    assert store.get_open_shadow_signals() == []


def test_migration_adds_mtum_columns_to_old_table(tmp_path):
    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE shadow_signals (signal_date TEXT, symbol TEXT, grp TEXT,"
        " entry_price REAL, PRIMARY KEY (signal_date, symbol, grp))"
    )
    conn.commit()
    conn.close()

    SQLiteStore(path=db)

    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(shadow_signals)")}
    assert "alpha_mtum_10d" in cols and "mtum_entry_price" in cols
