from __future__ import annotations

import pandas as pd

from src.indicators.technical import calc_atr_pct, technical_score


def _ohlcv(rows: int = 60, start: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    close = [start + i * step for i in range(rows)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [v + 1 for v in close],
            "Low": [v - 1 for v in close],
            "Close": close,
            "Volume": [1_000_000] * rows,
        }
    )


def test_technical_score_returns_zero_for_insufficient_data() -> None:
    score, reasons = technical_score(_ohlcv(rows=10))

    assert score == 0
    assert reasons == ["insufficient data"]


def test_technical_score_rewards_strong_setup_without_exceeding_cap() -> None:
    stock = _ohlcv(rows=260, start=20, step=1.0)
    spy = _ohlcv(rows=260, start=100, step=0.05)
    stock.loc[stock.index[-1], "Volume"] = 5_000_000

    score, reasons = technical_score(stock, spy)

    assert 0 <= score <= 30
    assert score >= 24
    assert any("MA alignment" in reason for reason in reasons)
    assert any("Outperforming SPY" in reason for reason in reasons)


def test_calc_atr_pct_handles_empty_and_positive_data() -> None:
    assert calc_atr_pct(pd.DataFrame()) == 0.0

    atr_pct = calc_atr_pct(_ohlcv(rows=30), n=14)

    assert atr_pct > 0
