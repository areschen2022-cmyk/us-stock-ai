"""Regression tests for the morning-brief staleness banner.

7/21-24 incident: the evening pipeline crashed, run_morning_telegram fell back
to the last scored date, and four consecutive briefs served 7/20 data with no
visible sign anything was wrong. Lag beyond 2 trading days must now be loud.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.notifier.telegram import _build_morning_report

_PRICES = {"SPY": 600.0, "QQQ": 500.0, "^VIX": 15.0}


def _render(overview: dict, today: date) -> str:
    msg = _build_morning_report([], _PRICES, today, overview=overview)
    return msg[0] if isinstance(msg, list) else msg


def test_incident_lag_exceeds_threshold():
    """The real outage — 7/20 data on 7/24 — must clear the >2 trading-day bar."""
    assert int(np.busday_count(date(2026, 7, 20), date(2026, 7, 24))) == 4


def test_weekend_gap_does_not_trip_threshold():
    """Fri data read on Mon is 1 trading day — normal, must stay silent."""
    assert int(np.busday_count(date(2026, 7, 24), date(2026, 7, 27))) == 1


def test_warning_renders_above_the_header():
    overview = {
        "data_date": "2026-07-20",
        "stale_warning": "⚠️ 資料已落後 4 個交易日（資料日 2026-07-20）— 晚間更新管線可能中斷",
        "total_scanned": 35,
    }
    text = _render(overview, date(2026, 7, 24))
    assert text.startswith("⚠️ 資料已落後 4 個交易日")
    assert "美股 AI 早報" in text


def test_fresh_data_renders_unchanged():
    text = _render({"data_date": "2026-07-24", "total_scanned": 35}, date(2026, 7, 24))
    assert text.startswith("美股 AI 早報")
    assert "資料已落後" not in text
