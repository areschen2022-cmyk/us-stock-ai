"""Weekly full-market v2 S-grade scan -> watchlist candidate feed.

Rationale (kp_us_score_v2_sp500_validation): alpha's biggest driver is
UNIVERSE selection (watchlist-S alpha20 +2.39% vs full-pool-S +0.40%), while
v2 ranks correctly inside any pool. So run the score across the whole current
S&P 500 and surface non-watchlist S-grade names as systematic watchlist-add
candidates — turning pool curation from intuition into a pipeline.

Writes data/market_scan.json; the dashboard reads it (same cached-snapshot
pattern as trading_hub_context.json). Runs Mondays in CI (skips other days
unless --force) to keep the daily pipeline lean.

Usage: python scripts/scan_market_candidates.py [--force] [--top 15]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.strategy import us_market
from src.scoring.grade import grade_label
from scripts.backtest_score_v2_sp500 import get_sp500_symbols
from main import get_config

_OUT = _REPO_ROOT / "data" / "market_scan.json"
_HISTORY_DAYS = 400  # enough for 252d high/low + 12m momentum


def scan(top_n: int) -> dict:
    watch = set(get_config().get("symbols", []))
    universe = sorted(set(get_sp500_symbols()) | watch)
    print(f"[Scan] Fetching ~2y history for {len(universe)} symbols...")

    frames: dict[str, pd.DataFrame] = {}
    batch = 120
    for i in range(0, len(universe), batch):
        chunk = universe[i:i + batch]
        raw = yf.download(chunk, period="2y", group_by="ticker",
                          auto_adjust=True, progress=False, threads=True)
        for sym in chunk:
            try:
                df = raw[sym].dropna(how="all") if len(chunk) > 1 else raw
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.dropna(subset=["Close"])
                if len(df) >= 280:
                    frames[sym] = df.tail(_HISTORY_DAYS)
            except Exception:
                pass
    print(f"[Scan] {len(frames)} symbols with sufficient history")

    composites: dict[str, float] = {}
    for sym, df in frames.items():
        close = df["Close"].astype(float)
        i = len(close) - 1
        parts = []
        if i >= 126:
            parts.append(float(close.iloc[i] / close.iloc[i - 126] - 1))
        if i >= 252 + 21:
            parts.append(float(close.iloc[i - 21] / close.iloc[i - 252 - 21] - 1))
        if parts:
            composites[sym] = sum(parts) / len(parts)
    rs_pct = us_market.rs_percentile(composites)

    rows = []
    for sym, df in frames.items():
        if sym not in composites:
            continue
        price = float(df["Close"].astype(float).iloc[-1])
        if not us_market.liquidity_gate(df, price).get("passed"):
            continue
        total, parts = us_market.score_v2(df, rs_pct.get(sym))
        grade = grade_label(total)
        if grade != "S":
            continue
        rows.append({
            "symbol": sym,
            "score_v2": total,
            "rs_rating": rs_pct.get(sym),
            "weekly_up": us_market.weekly_direction_up(df),
            "price": round(price, 2),
            "in_watchlist": sym in watch,
            "parts": parts,
        })

    rows.sort(key=lambda r: r["score_v2"], reverse=True)
    candidates = [r for r in rows if not r["in_watchlist"]][:top_n]
    return {
        "generated_at": str(date.today()),
        "universe_size": len(frames),
        "s_grade_total": len(rows),
        "s_grade_in_watchlist": sum(1 for r in rows if r["in_watchlist"]),
        "candidates": candidates,
        "note": "全市場 v2 S 級掃描（watchlist 候補）。依據 kp_us_score_v2_sp500_validation："
                "選池是 alpha 主要來源，此管道把選池系統化。候補僅供研究，加入 watchlist 由人工決定。",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="run regardless of weekday")
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()

    if not args.force and date.today().weekday() != 0:
        print("[Scan] Not Monday — skipping (use --force to override)")
        sys.exit(0)

    result = scan(args.top)
    _OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Scan] Written {_OUT}: {result['s_grade_total']} S-grade, "
          f"{len(result['candidates'])} non-watchlist candidates")
    for c in result["candidates"][:10]:
        print(f"  {c['symbol']:6s} v2={c['score_v2']} RS={c['rs_rating']} weekly_up={c['weekly_up']}")
