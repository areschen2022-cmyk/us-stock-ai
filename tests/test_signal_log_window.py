"""The 選股記錄 tab must show signals old enough to have returns.

2026-08-12, user-reported: every row read "追蹤中" with empty return columns.
The rows themselves were correct — a 5-day return needs 5 trading days — but
the query's 60-row limit spanned only ~4 days at ~20 signals/day, so every row
on the tab was younger than its own 5-day column. 406 settled signals existed
in the database and none could ever reach the page: a forward-return tracker
that structurally could not display a forward return.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import _signal_log_summary
from src.storage.sqlite_store import SQLiteStore

_SETTLE_HORIZON_TRADING_DAYS = 10
_SIGNALS_PER_DAY = 20          # observed: 16-25 across live_top/score_v2_sa/ai_buy


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(path=tmp_path / "t.sqlite3")


def test_default_window_spans_past_the_settlement_horizon(store):
    """Seed more days than the 10d horizon and require settled rows to survive
    the limit — the property that was violated in production."""
    start = date(2026, 6, 1)
    for day in range(20):                       # 20 days x 20 signals = 400 rows
        d = start + timedelta(days=day)
        settled = day < 12                      # older half has returns
        for i in range(_SIGNALS_PER_DAY):
            store.upsert_shadow_signal(d, "live_top", {
                "symbol": f"S{i:02d}", "entry_price": 10.0,
                "return_5d": 1.0 if settled else None,
                "return_10d": 2.0 if settled else None,
            })
    with store._connect() as conn:               # returns are not insert columns
        conn.execute("UPDATE shadow_signals SET return_5d=1.0, return_10d=2.0 "
                     "WHERE signal_date < ?", (str(start + timedelta(days=12)),))

    rows = store.get_recent_shadow_signals()

    days = {r["signal_date"] for r in rows}
    assert len(days) > _SETTLE_HORIZON_TRADING_DAYS, (
        f"window covers only {len(days)} days — cannot reach a 10-day return")
    assert any(r["return_5d"] is not None for r in rows), "no settled row survived the limit"


def test_summary_reports_distinct_symbols_not_just_rows():
    """One stock re-signalled daily is one bet. The tab's headline average must
    carry n_symbols or it overstates its own evidence (CLAUDE.md rule)."""
    rows = [{"symbol": "AAA", "return_5d": 5.0, "return_10d": None} for _ in range(30)]
    rows += [{"symbol": "BBB", "return_5d": -1.0, "return_10d": None} for _ in range(10)]

    s = _signal_log_summary(rows)["settled_5d"]

    assert s["n"] == 40
    assert s["n_symbols"] == 2


def test_summary_counts_tracking_rows_separately():
    rows = [{"symbol": "A", "return_5d": None, "return_10d": None},
            {"symbol": "B", "return_5d": 1.0, "return_10d": None}]
    s = _signal_log_summary(rows)
    assert s["tracking"] == 1
    assert s["settled_10d"] is None      # nothing settled at 10d yet


def test_summary_survives_an_all_tracking_window():
    """The legitimate early state — must not crash or fabricate a number."""
    rows = [{"symbol": "A", "return_5d": None, "return_10d": None}]
    s = _signal_log_summary(rows)
    assert s == {"tracking": 1, "settled_5d": None, "settled_10d": None}
