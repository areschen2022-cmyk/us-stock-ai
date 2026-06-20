"""RSS news fetcher for major US financial news sources."""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path

import feedparser

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

RSS_FEEDS = {
    "reuters_markets": "https://feeds.reuters.com/reuters/businessNews",
    "marketwatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "seeking_alpha": "https://seekingalpha.com/feed.xml",
    "yahoo_finance": "https://finance.yahoo.com/news/rssindex",
    "benzinga": "https://www.benzinga.com/feed",
}


def fetch_news(max_per_feed: int = 20, cache_hours: int = 2) -> list[dict]:
    """Fetch and merge news from all RSS feeds."""
    cache_key = _CACHE_DIR / f"news_{date.today()}.json"
    if cache_key.exists():
        age_h = (time.time() - cache_key.stat().st_mtime) / 3600
        if age_h < cache_hours:
            try:
                return json.loads(cache_key.read_text())
            except Exception:
                pass

    all_items: list[dict] = []
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                published = entry.get("published", "")
                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = entry.get("summary", "")
                all_items.append({
                    "source": source,
                    "title": title,
                    "url": link,
                    "summary": summary[:400],
                    "published": published,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as e:
            logger.warning("RSS fetch failed for %s: %s", source, e)

    cache_key.write_text(json.dumps(all_items, default=str))
    logger.info("Fetched %d news items from %d feeds", len(all_items), len(RSS_FEEDS))
    return all_items


def score_news_catalyst(symbol: str, company_name: str, news_items: list[dict]) -> tuple[int, list[str]]:
    """
    Simple keyword-based catalyst scoring. Returns (score 0-15, matched headlines).
    For top candidates, AI council will do deeper analysis.
    """
    score = 0
    matched: list[str] = []
    name_lower = (company_name or symbol).lower()
    sym_lower = symbol.lower()

    positive_keywords = [
        "beat", "record", "growth", "partnership", "contract", "upgrade",
        "buyback", "dividend", "breakthrough", "expansion", "acquisition",
        "revenue", "earnings beat", "raised guidance", "new product",
    ]
    negative_keywords = [
        "miss", "downgrade", "recall", "investigation", "lawsuit", "layoff",
        "fraud", "loss", "cut guidance", "warning", "probe", "decline",
    ]

    for item in news_items:
        title = item.get("title", "").lower()
        if sym_lower not in title and name_lower not in title:
            continue
        matched.append(item["title"])
        for kw in positive_keywords:
            if kw in title:
                score += 3
                break
        for kw in negative_keywords:
            if kw in title:
                score -= 3
                break

    return max(0, min(score, 15)), matched[:5]
