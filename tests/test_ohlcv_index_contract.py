"""Regression tests for the OHLCV DatetimeIndex contract.

Root cause of the 7/21-24 holiday-gate outage: fetch_ohlcv stringified the
index before JSON-caching it and returned the stringified frame, so every
consumer doing `df.index[-1].date() < some_date` crashed on a str. Both the
fresh-fetch path and the cache-hit path must hand back a DatetimeIndex.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data_provider import yfinance_client as yc


@pytest.fixture
def fake_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(yc, "_CACHE_DIR", tmp_path)
    return tmp_path


def test_cache_hit_returns_datetime_index(fake_cache):
    """A cache written with string dates must be rehydrated as a DatetimeIndex."""
    frame = pd.DataFrame(
        {"Close": [10.0, 11.0], "Volume": [100, 200]},
        index=["2026-07-24", "2026-07-25"],
    )
    key = f"ohlcv_TEST_6mo_{date.today()}"
    (fake_cache / f"yf_{key}.json").write_text(json.dumps(frame.to_dict(), default=str))

    df = yc.fetch_ohlcv("TEST", period="6mo")

    assert not df.empty
    assert isinstance(df.index, pd.DatetimeIndex)
    # the whole point: this comparison used to raise TypeError
    assert df.index[-1].date() >= date(2026, 7, 25)


def test_cache_hit_survives_unparseable_index(fake_cache):
    """A corrupt index must not blow up the fetch — degrade, don't crash."""
    key = f"ohlcv_JUNK_6mo_{date.today()}"
    (fake_cache / f"yf_{key}.json").write_text(
        json.dumps({"Close": {"not-a-date": 1.0, "also-junk": 2.0}})
    )

    df = yc.fetch_ohlcv("JUNK", period="6mo")

    assert len(df) == 2


def test_batch_path_returns_datetime_index(monkeypatch):
    """The large-batch branch feeds main.py's holiday gate. It used to stringify
    the index while the <=10-symbol branch did not, so the index type silently
    depended on batch size. Every symbol on every branch returns a DatetimeIndex.
    """
    symbols = [f"S{i}" for i in range(12)]
    idx = pd.DatetimeIndex(["2026-07-24", "2026-07-27"])
    raw = pd.concat(
        {s: pd.DataFrame({"Close": [1.0, 2.0], "Volume": [10, 20]}, index=idx)
         for s in symbols},
        axis=1,
    )
    monkeypatch.setattr(yc.yf, "download", lambda *a, **k: raw)

    result = yc.fetch_batch_ohlcv(symbols, period="2y")

    assert set(result) == set(symbols)
    for sym, df in result.items():
        assert isinstance(df.index, pd.DatetimeIndex), f"{sym} lost the contract"
        # the holiday gate's exact operation
        assert df.index[-1].date() == date(2026, 7, 27)
