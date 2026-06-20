from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.scoring.score_engine import compute_score


def _ohlcv(rows: int = 260, start: float = 50.0, step: float = 0.8) -> pd.DataFrame:
    close = [start + i * step for i in range(rows)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [v + 1.5 for v in close],
            "Low": [v - 1.5 for v in close],
            "Close": close,
            "Volume": [2_000_000] * rows,
        }
    )


def test_compute_score_returns_consistent_stock_score() -> None:
    ohlcv = _ohlcv()
    spy = _ohlcv(start=400, step=0.1)
    ohlcv.loc[ohlcv.index[-1], "Volume"] = 5_000_000
    info = {
        "shortName": "NVIDIA Corp.",
        "sector": "Technology",
        "industry": "Semiconductors",
        "marketCap": 3_000_000_000_000,
        "revenueGrowth": 0.25,
        "grossMargins": 0.72,
        "operatingMargins": 0.45,
        "freeCashflow": 10_000_000_000,
        "debtToEquity": 20,
        "trailingEps": 12.3,
        "institutionPercentHeld": 0.74,
        "insiderPercentHeld": 0.06,
    }
    news_items = [
        {
            "title": "NVIDIA announces new AI chip partnership and raised guidance",
            "source": "test",
        }
    ]

    score = compute_score(
        symbol="NVDA",
        ohlcv=ohlcv,
        info=info,
        market_index_prices={"SPY": 620, "QQQ": 540, "^VIX": 14, "TLT": 90, "HYG": 80},
        news_items=news_items,
        spy_ohlcv=spy,
        revenue_yoy=0.32,
        insider_data={"buys": 2, "sells": 0},
        earnings_cal={"Earnings Date": date(2026, 7, 15).isoformat()},
        today=date(2026, 6, 20),
    )

    assert score.symbol == "NVDA"
    assert score.name == "NVIDIA Corp."
    assert 0 <= score.total_score <= 100
    assert score.grade in {"S", "A", "B", "C", "D"}
    assert score.technical_score <= 30
    assert score.fundamental_score <= 20
    assert score.flow_score <= 15
    assert score.news_catalyst_score <= 15
    assert score.market_sentiment_score <= 10
    assert score.risk_penalty <= 10
    assert score.price == float(ohlcv["Close"].iloc[-1])
    assert "ai_infra" in score.themes
    assert score.stop_price is not None


def test_compute_score_applies_earnings_risk_penalty() -> None:
    score = compute_score(
        symbol="AAPL",
        ohlcv=_ohlcv(),
        info={"shortName": "Apple", "marketCap": 3_000_000_000_000},
        market_index_prices={"SPY": 620, "^VIX": 18},
        news_items=[],
        earnings_cal={"Earnings Date": (date(2026, 6, 20) + timedelta(days=3)).isoformat()},
        today=date(2026, 6, 20),
    )

    assert score.risk_penalty >= 6
    assert any("Earnings in 3d" in warning for warning in score.warnings)
