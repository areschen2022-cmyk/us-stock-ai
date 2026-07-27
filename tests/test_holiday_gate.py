"""Regression: the holiday gate must survive CI's string-typed OHLCV index.

2026-07-24 incident: `spy_ohlcv.index[-1]` is a plain string on the CI data
path; the gate compared `str < date` and every >=21 UTC scheduled run crashed
(TypeError), silently halting evening dashboard updates for four days.
"""
from datetime import date

import main as M


def test_bar_date_accepts_string_index():
    assert M._bar_date("2026-07-24") == date(2026, 7, 24)
    assert M._bar_date("2026-07-24 00:00:00") == date(2026, 7, 24)


def test_bar_date_accepts_timestamp_like():
    import pandas as pd
    assert M._bar_date(pd.Timestamp("2026-07-24")) == date(2026, 7, 24)


def test_bar_date_unparseable_returns_none():
    assert M._bar_date("not-a-date") is None
    assert M._bar_date(None) is None
