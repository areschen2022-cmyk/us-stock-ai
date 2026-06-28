"""Social/retail sentiment via StockTwits public API (free, no auth).

Ported in spirit from ZhuLinsen/daily_stock_analysis's social-sentiment idea,
but using StockTwits (purpose-built for equities; users tag messages
Bullish/Bearish) instead of Reddit/X — Reddit blocks datacenter IPs and X is
paid. Advisory/shadow signal: surfaced and logged, NOT folded into the grade
until forward-return validation justifies it.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_BASE = "https://api.stocktwits.com/api/2/streams/symbol/{}.json"
_UA = "Mozilla/5.0 (compatible; us-stock-ai/1.0)"


def _label(ratio: float | None, msgs: int) -> str:
    if ratio is None or msgs < 3:
        return "資料不足"
    if ratio >= 0.4:
        return "強烈看多"
    if ratio >= 0.15:
        return "偏多"
    if ratio <= -0.4:
        return "強烈看空"
    if ratio <= -0.15:
        return "偏空"
    return "中性"


def fetch_stocktwits_sentiment(symbol: str, timeout: int = 12) -> dict[str, Any]:
    """Return retail-sentiment snapshot for one ticker. Empty/safe dict on any
    failure (rate-limit, network, delisted) so the pipeline never breaks.

    Fields: messages, bullish, bearish, sentiment_ratio (-1..1), watchlist_count,
    label, score_0_10 (advisory bullishness)."""
    out: dict[str, Any] = {
        "messages": 0, "bullish": 0, "bearish": 0, "sentiment_ratio": None,
        "watchlist_count": None, "label": "資料不足", "score_0_10": None,
    }
    try:
        req = urllib.request.Request(_BASE.format(symbol), headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except Exception as e:
        logger.warning("StockTwits failed for %s: %s", symbol, e)
        return out

    msgs = data.get("messages", []) or []
    bull = bear = 0
    for m in msgs:
        ent = m.get("entities") or {}
        sent = (ent.get("sentiment") or {}).get("basic") if ent.get("sentiment") else None
        if sent == "Bullish":
            bull += 1
        elif sent == "Bearish":
            bear += 1

    tagged = bull + bear
    ratio = round((bull - bear) / tagged, 2) if tagged else None
    out.update({
        "messages": len(msgs),
        "bullish": bull,
        "bearish": bear,
        "sentiment_ratio": ratio,
        "watchlist_count": (data.get("symbol") or {}).get("watchlist_count"),
        "label": _label(ratio, len(msgs)),
    })
    # advisory 0-10 bullishness: map ratio [-1,1] → [0,10], None when no tags
    if ratio is not None:
        out["score_0_10"] = round((ratio + 1) / 2 * 10, 1)
    return out
