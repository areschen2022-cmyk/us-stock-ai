"""MA20 trail is the primary exit; the 2xATR level is a disaster floor.

Promoted by the 2026-07 month-end review (n=62): MA20-trail -1.13% vs 20d-hold
-5.86% vs 2xATR -7.01%, paired t=4.76. The 2xATR number must stay visible —
it is an intraday hard stop guarding gap risk, a different failure mode from a
close-based trailing rule — but it must no longer read as *the* exit.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.notifier.telegram import _build_morning_report

_PRICES = {"SPY": 600.0, "QQQ": 500.0, "^VIX": 15.0}


def _score(symbol="AAA", price=100.0, stop=88.0, grade="S", total=90):
    return SimpleNamespace(
        symbol=symbol, name=symbol, total_score=total, grade=grade,
        action="buy", price=price, stop_price=stop, themes=[],
        technical_score=20, fundamental_score=20, flow_score=10,
        news_catalyst_score=5, market_sentiment_score=5, risk_penalty=0,
        atr_pct=3.0,
    )


def _render(scores, overview):
    msg = _build_morning_report(scores, _PRICES, date(2026, 7, 28), overview=overview)
    return msg[0] if isinstance(msg, list) else msg


def test_ma20_is_the_primary_exit_and_atr_is_the_floor():
    s = _score()
    text = _render([s], {
        "total_scanned": 1, "grade_s": 1,
        "ma20_exit_map": {"AAA": {"level": 95.0, "below": False}},
    })
    assert "收盤跌破 MA20 $95.00" in text
    assert "災難停損 $88.00" in text
    # MA20 must be presented before the ATR level, not after it
    assert text.index("MA20 $95.00") < text.index("災難停損")


def test_already_below_ma20_says_exit_now():
    """A name closing under its MA20 has already triggered the rule — showing a
    'target' above the current price would invert the instruction."""
    s = _score(price=90.0)
    text = _render([s], {
        "total_scanned": 1, "grade_s": 1,
        "ma20_exit_map": {"AAA": {"level": 95.0, "below": True}},
    })
    assert "已跌破 MA20" in text and "依規則出場" in text


def test_missing_ma20_falls_back_to_the_atr_stop():
    """Short history (<20 bars) yields no MA20; guidance must not vanish."""
    s = _score()
    text = _render([s], {"total_scanned": 1, "grade_s": 1, "ma20_exit_map": {}})
    assert "停損 $88.00" in text
    assert "MA20" not in text


def test_breached_name_is_hoisted_into_its_own_alert_block():
    """A breach can rank into the '其他追蹤標的' tail, which prints no exit
    detail — the alert must appear regardless of where the name ranks."""
    strong, weak = _score("AAA", 100.0, 88.0, "S", 90), _score("ZZZ", 50.0, 44.0, "D", 30)
    text = _render([strong, weak], {
        "total_scanned": 2, "grade_s": 1,
        "ma20_exit_map": {"AAA": {"level": 95.0, "below": False},
                          "ZZZ": {"level": 55.0, "below": True}},
    })
    assert "出場警示" in text
    assert "ZZZ｜現價 $50.00｜MA20 $55.00" in text
    # ranked last, but the alert precedes the new-idea sections
    assert text.index("出場警示") < text.index("今日重點")


def test_no_breach_means_no_alert_block():
    s = _score()
    text = _render([s], {
        "total_scanned": 1, "grade_s": 1,
        "ma20_exit_map": {"AAA": {"level": 95.0, "below": False}},
    })
    assert "出場警示" not in text


def test_no_exit_data_at_all_does_not_crash():
    s = _score(stop=None)
    text = _render([s], {"total_scanned": 1, "grade_s": 1})
    assert "AAA" in text
