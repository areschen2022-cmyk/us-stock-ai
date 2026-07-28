"""The 2xATR arm of the exit comparison must use the 20d stop window.

Month-end 2026-07-28: `_exit_comparison` reused `stop_hit`, which is scoped to
the 10d outcome window (Codex audit-2 #7). Over a 20-day hold that missed
11 of 15 real stop touches, so the 2xATR column rendered byte-identical to
buy-and-hold and the exit decision was being made on a flat line.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.report.performance import build_performance_payload


class _FakeStore:
    """Minimal stand-in: build_performance_payload only needs _connect()."""

    def __init__(self, rows):
        self._rows = rows

    def _connect(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE shadow_signals (grp TEXT, symbol TEXT, signal_date TEXT,"
            " entry_price REAL, stop_price REAL, stop_hit INT, stop_hit_20d INT,"
            " return_5d REAL, return_10d REAL, return_20d REAL,"
            " ma20_exit_return REAL, failure_reason TEXT, entry_quality TEXT,"
            " live_grade TEXT)"
        )
        conn.execute("CREATE TABLE watch_signals (action TEXT, themes_json TEXT,"
                     " return_5d REAL, return_10d REAL, stop_hit INT, failure_reason TEXT)")
        conn.executemany(
            "INSERT INTO shadow_signals (grp,symbol,signal_date,entry_price,stop_price,"
            "stop_hit,stop_hit_20d,return_20d,ma20_exit_return) VALUES (?,?,?,?,?,?,?,?,?)",
            self._rows,
        )
        conn.commit()
        return conn


def _rows_with_late_stops(n=6):
    """Every signal: stop NOT touched by day 10, but touched by day 20, and the
    20d hold ends flat. A correct 2xATR arm books the -10% stop; the buggy one
    reports the flat hold return."""
    return [
        ("live_top", f"S{i}", "2026-06-25", 100.0, 90.0, 0, 1, 0.0, -2.0)
        for i in range(n)
    ]


def test_2atr_arm_uses_20d_window():
    payload = build_performance_payload(_FakeStore(_rows_with_late_stops()))
    cmp_ = payload["exit_comparison"]["live_top"]

    assert cmp_["n"] == 6
    assert cmp_["hold20_avg"] == 0.0
    # the stop was touched inside the 20d hold -> booked at -10%, NOT the flat hold
    assert cmp_["stop2atr_avg"] == -10.0, "2xATR arm ignored the 20d stop window"
    assert cmp_["stop2atr_avg"] != cmp_["hold20_avg"]


def test_untouched_stop_rides_to_20d():
    rows = [("live_top", f"S{i}", "2026-06-25", 100.0, 90.0, 0, 0, 3.0, 1.0)
            for i in range(6)]
    cmp_ = build_performance_payload(_FakeStore(rows))["exit_comparison"]["live_top"]

    assert cmp_["stop2atr_avg"] == 3.0  # never hit -> same as holding
