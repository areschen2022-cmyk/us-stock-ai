"""US Stock ScoreEngine — 100-point composite scoring."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from src.indicators.technical import technical_score, calc_atr_pct
from src.indicators.fundamental import fundamental_score
from src.indicators.market import market_sentiment_score
from src.indicators.risk import risk_score
from src.indicators.flow import flow_score
from src.news.rss_fetcher import score_news_catalyst, score_symbol_news
from src.news.theme_detector import get_symbol_themes, theme_catalyst_score
from src.news.catalyst_confidence import classify_catalyst_confidence
from src.scoring.grade import grade_label, action_from_grade


@dataclass
class StockScore:
    symbol: str
    name: str
    total_score: int
    grade: str
    action: str
    price: float | None

    technical_score: int
    fundamental_score: int
    flow_score: int
    news_catalyst_score: int
    market_sentiment_score: int
    risk_penalty: int

    themes: list[str] = field(default_factory=list)
    reasons: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    matched_headlines: list[str] = field(default_factory=list)
    catalyst_confidence: dict[str, Any] = field(default_factory=dict)
    atr_pct: float = 0.0
    sector: str = ""
    industry: str = ""
    market_cap: float = 0.0

    entry_price: float | None = None
    stop_price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_score(
    symbol: str,
    ohlcv: pd.DataFrame,
    info: dict[str, Any],
    market_index_prices: dict[str, float],
    news_items: list[dict],
    spy_ohlcv: pd.DataFrame | None = None,
    revenue_yoy: float | None = None,
    insider_data: dict | None = None,
    earnings_cal: dict | None = None,
    today: date | None = None,
    symbol_news: list[dict] | None = None,
) -> StockScore:
    today = today or date.today()
    name = info.get("shortName") or info.get("longName") or symbol
    price = float(ohlcv["Close"].iloc[-1]) if not ohlcv.empty else None

    # Sub-scores
    t_score, t_reasons = technical_score(ohlcv, spy_ohlcv)
    f_score, f_reasons = fundamental_score(info, revenue_yoy)
    fl_score, fl_reasons = flow_score(info, insider_data)
    m_score, m_reasons = market_sentiment_score(market_index_prices)

    # News catalyst — prefer per-ticker yfinance news (relevant); fall back to
    # the shared general-RSS pool with name/ticker matching.
    if symbol_news:
        raw_news_score, matched_headlines = score_symbol_news(symbol_news)
    else:
        raw_news_score, matched_headlines = score_news_catalyst(symbol, name, news_items)
    theme_bonus = theme_catalyst_score(symbol, matched_headlines)
    n_score = min(raw_news_score + theme_bonus, 15)
    n_reasons = matched_headlines[:3]

    # Risk (penalty)
    atr_pct = calc_atr_pct(ohlcv)
    r_penalty, r_warnings = risk_score(atr_pct, earnings_cal or {}, info, today)

    # Total: 100 - risk_penalty = max possible considering risk
    raw_total = t_score + f_score + fl_score + n_score + m_score
    total = max(0, raw_total - r_penalty)

    grade = grade_label(total)
    action = action_from_grade(grade, r_penalty)
    themes = get_symbol_themes(symbol)

    # Entry/stop suggestion
    stop_price: float | None = None
    if price and atr_pct > 0:
        stop_price = round(price * (1 - atr_pct / 100 * 2), 2)

    return StockScore(
        symbol=symbol,
        name=name,
        total_score=total,
        grade=grade,
        action=action,
        price=price,
        technical_score=t_score,
        fundamental_score=f_score,
        flow_score=fl_score,
        news_catalyst_score=n_score,
        market_sentiment_score=m_score,
        risk_penalty=r_penalty,
        themes=themes,
        reasons={
            "technical": t_reasons,
            "fundamental": f_reasons,
            "flow": fl_reasons,
            "market": m_reasons,
            "news": n_reasons,
        },
        warnings=r_warnings,
        matched_headlines=matched_headlines,
        atr_pct=atr_pct,
        sector=info.get("sector", ""),
        industry=info.get("industry", ""),
        market_cap=info.get("marketCap", 0),
        entry_price=price,
        stop_price=stop_price,
    )
