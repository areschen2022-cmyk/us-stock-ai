"""AI council: gates by score threshold, aggregates review."""
from __future__ import annotations

from datetime import date
from typing import Any

from src.ai.openrouter_client import OpenRouterClient
from src.scoring.score_engine import StockScore
from src.storage.sqlite_store import SQLiteStore

_SCORE_THRESHOLD = 75
_TOP_N_FALLBACK = 5
_MAX_REVIEWS = 8          # per-run cap: ~175 tokens/review keeps a Monday run
_SCAN_REVIEW_CAP = 5      # (8+5 reviews ≈ 2.3k) inside the 4k daily budget


def _build_summary(score: StockScore) -> str:
    return (
        f"Score={score.total_score} Grade={score.grade} Price=${score.price} "
        f"Technical={score.technical_score} Fundamental={score.fundamental_score} "
        f"Flow={score.flow_score} News={score.news_catalyst_score} "
        f"Market={score.market_sentiment_score} Risk_penalty={score.risk_penalty} "
        f"Themes={','.join(score.themes)} Warnings={'; '.join(score.warnings[:2])}"
    )


class ModelCouncil:
    def __init__(self, store: SQLiteStore | None = None) -> None:
        self.client = OpenRouterClient()
        self.store = store

    def select_candidates(
        self,
        scores: list[StockScore],
        priority_symbols: set[str] | None = None,
    ) -> list[StockScore]:
        """Return stocks eligible for AI review.

        Eligibility: live score>=75 (structurally unreachable under the C-grade
        ceiling, kept for after a recalibration) OR membership in
        priority_symbols — main passes the v2 S/A + weekly-up set, so the
        validated research tier drives coverage instead of the dead threshold.
        Falls back to top-5 by live score; capped at _MAX_REVIEWS."""
        pri = priority_symbols or set()
        eligible = [s for s in scores
                    if s.total_score >= _SCORE_THRESHOLD or s.symbol in pri]
        if not eligible:
            eligible = sorted(scores, key=lambda s: s.total_score, reverse=True)[:_TOP_N_FALLBACK]
        eligible.sort(key=lambda s: (s.symbol in pri, s.total_score), reverse=True)
        return eligible[:_MAX_REVIEWS]

    def review_scan_candidates(self, scan: dict | None, today: date | None = None) -> dict[str, dict[str, Any]]:
        """AI second opinion on full-market scan candidates — non-watchlist
        names with no StockScore, where an independent read adds the most
        value. Only runs when the scan snapshot is from today (Mondays),
        so stale candidates don't burn budget every day."""
        today = today or date.today()
        if not scan or scan.get("generated_at") != str(today):
            return {}
        results: dict[str, dict[str, Any]] = {}
        for row in (scan.get("candidates") or [])[:_SCAN_REVIEW_CAP]:
            summary = (
                f"Full-market v2 research scan hit: score_v2={row.get('score_v2')}/100 (S) "
                f"RS_rating={row.get('rs_rating')} weekly_trend_up={row.get('weekly_up')} "
                f"price=${row.get('price')} components={row.get('parts')}. "
                f"NOT in the current watchlist — assess as a potential watchlist addition."
            )
            review = self.client.single_review(row["symbol"], summary)
            review["score"] = row.get("score_v2")
            review["grade"] = "S(v2)"
            review["tokens_used"] = self.client.tokens_used
            results[row["symbol"]] = review
            if self.store:
                self.store.save_ai_review(today, row["symbol"], review)
        if results:
            print(f"[AI Council] Scan-candidate reviews: {len(results)}")
        return results

    def review(
        self,
        candidates: list[StockScore],
        today: date | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Run AI review for each candidate. Returns {symbol: review_dict}."""
        today = today or date.today()
        results: dict[str, dict[str, Any]] = {}

        for stock in candidates:
            summary = _build_summary(stock)
            review = self.client.single_review(stock.symbol, summary)
            review["score"] = stock.total_score
            review["grade"] = stock.grade
            review["tokens_used"] = self.client.tokens_used
            results[stock.symbol] = review

            if self.store:
                self.store.save_ai_review(today, stock.symbol, review)

        print(f"[AI Council] Reviewed {len(results)} stocks | tokens used: {self.client.tokens_used}")
        return results

    def get_ai_summaries(self, reviews: dict[str, dict]) -> dict[str, str]:
        """Compact {symbol: 'action: reason'} for Telegram."""
        return {
            sym: f"{r.get('action','?')} (conf={r.get('confidence',0):.0%}): {r.get('reason','')}"
            for sym, r in reviews.items()
        }
