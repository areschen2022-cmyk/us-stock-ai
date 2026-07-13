"""Backtest the weekly candidate-scan RULE itself: every week, take the
top-15 v2 S-grade names across the full current S&P 500 (exactly what
scan_market_candidates.py produces) and measure the equal-weight basket's
forward 5/20d return vs SPY.

This validates the pipeline's selection rule as a PORTFOLIO (what a user
acting on the candidate list would experience), not just per-signal buckets.
Same declared residual bias as backtest_score_v2_sp500.py (current
constituents, not point-in-time).

Usage: python scripts/backtest_scan_rule.py [--years 10] [--top 15]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.strategy import us_market
from src.scoring.grade import grade_label
from scripts.backtest_score_v2_sp500 import get_sp500_symbols, fetch_all
from main import get_config

_WARMUP_DAYS = 280
_STEP = 5  # weekly


def run(years: int, top_n: int) -> list[dict]:
    universe = sorted(set(get_sp500_symbols()) | set(get_config().get("symbols", [])))
    data = fetch_all(universe + ["SPY"], years)
    spy_df = data.pop("SPY", None)
    if spy_df is None or spy_df.empty:
        raise RuntimeError("SPY fetch failed")
    spy_close = spy_df["Close"].astype(float)
    spy_index = spy_df.index
    n_days = len(spy_index)

    weeks: list[dict] = []
    for day_i in range(_WARMUP_DAYS, n_days - 21, _STEP):
        signal_date = spy_index[day_i]
        windows: dict[str, pd.DataFrame] = {}
        composites: dict[str, float] = {}
        for sym, df in data.items():
            try:
                pos = df.index.get_indexer([signal_date], method="nearest")[0]
            except Exception:
                continue
            if pos < 0 or abs((df.index[pos] - signal_date).days) > 5:
                continue
            window = df.iloc[: pos + 1]
            if len(window) < _WARMUP_DAYS:
                continue
            close = window["Close"].astype(float)
            i = len(close) - 1
            parts = []
            if i >= 126:
                parts.append(float(close.iloc[i] / close.iloc[i - 126] - 1))
            if i >= 252 + 21:
                parts.append(float(close.iloc[i - 21] / close.iloc[i - 252 - 21] - 1))
            if parts:
                windows[sym] = window
                composites[sym] = sum(parts) / len(parts)
        if len(composites) < 100:
            continue
        rs_pct = us_market.rs_percentile(composites)

        picks: list[tuple[str, int]] = []
        for sym, window in windows.items():
            price = float(window["Close"].astype(float).iloc[-1])
            if not us_market.liquidity_gate(window, price).get("passed"):
                continue
            total, _ = us_market.score_v2(window, rs_pct.get(sym))
            if grade_label(total) == "S":
                picks.append((sym, total))
        picks.sort(key=lambda t: t[1], reverse=True)
        basket = [s for s, _ in picks[:top_n]]
        if not basket:
            continue

        spy_entry = float(spy_close.iloc[day_i])
        rets: dict[int, list[float]] = {5: [], 20: []}
        for sym in basket:
            full = data[sym]
            entry_pos = full.index.get_loc(windows[sym].index[-1])
            price = float(full["Close"].iloc[entry_pos])
            for h in (5, 20):
                if entry_pos + h < len(full):
                    rets[h].append(float(full["Close"].iloc[entry_pos + h]) / price - 1)
        rec = {"date": str(signal_date.date())[:10], "n_picks": len(basket),
               "s_grade_total": len(picks)}
        for h in (5, 20):
            if rets[h] and day_i + h < len(spy_close):
                basket_r = sum(rets[h]) / len(rets[h]) * 100
                spy_r = (float(spy_close.iloc[day_i + h]) / spy_entry - 1) * 100
                rec[f"ret_{h}d"] = round(basket_r, 2)
                rec[f"alpha_{h}d"] = round(basket_r - spy_r, 2)
        weeks.append(rec)
        if len(weeks) % 50 == 0:
            print(f"[ScanRule] ...{rec['date']} weeks={len(weeks)}")
    return weeks


def summarize(weeks: list[dict]) -> dict:
    df = pd.DataFrame(weeks)
    out: dict = {"n_weeks": len(df),
                 "avg_picks_per_week": round(float(df["n_picks"].mean()), 1),
                 "avg_s_grade_pool": round(float(df["s_grade_total"].mean()), 1)}
    for h in (5, 20):
        a = df[f"alpha_{h}d"].dropna()
        r = df[f"ret_{h}d"].dropna()
        out[f"basket_{h}d"] = {
            "avg_ret": round(float(r.mean()), 2),
            "avg_alpha": round(float(a.mean()), 2),
            "alpha_win_rate": round(float((a > 0).mean() * 100), 1),
            "t_stat_alpha": round(float(a.mean() / (a.std() / (len(a) ** 0.5))), 2) if len(a) > 1 else None,
        }
    df["year"] = pd.to_datetime(df["date"]).dt.year
    out["alpha20_by_year"] = {
        str(yr): {"n_weeks": len(g), "avg_alpha20": round(float(g["alpha_20d"].dropna().mean()), 2),
                  "win": round(float((g["alpha_20d"].dropna() > 0).mean() * 100), 1)}
        for yr, g in df.groupby("year")
    }
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()

    weeks = run(args.years, args.top)
    summary = summarize(weeks)
    out_path = _REPO_ROOT / "data" / f"backtest_scan_rule_{args.years}y.json"
    out_path.write_text(json.dumps({
        "generated_at": str(date.today()),
        "rule": f"weekly top-{args.top} v2 S-grade across current S&P 500, equal weight",
        "known_bias": "current constituents (not point-in-time); no costs (weekly research list, not an execution sim)",
        "summary": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ScanRule] Written {out_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
