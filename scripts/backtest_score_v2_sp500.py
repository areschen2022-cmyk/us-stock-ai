"""Score v2 on the FULL current S&P 500 — the survivorship-bias stress test.

The 40-name watchlist backtests carry a declared bias: the universe was
curated BECAUSE these are today's winners. This run re-validates score v2 on
all current S&P 500 constituents (Wikipedia) plus the watchlist. If the
S/A-bucket edge collapses here, it was a curation artifact; if it holds
(likely thinner), the score generalizes.

REMAINING BIAS (declared): current constituents are still not point-in-time —
companies that fell OUT of the index over the decade are absent. That residual
bias is far smaller than a hand-picked momentum list (index membership turns
over ~5%/yr vs a 100% curated-winners pool) and it inflates the WHOLE
universe's baseline, so the S-vs-rest SPREAD remains informative.

Usage: python scripts/backtest_score_v2_sp500.py [--years 10] [--step 5]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.strategy import us_market
from src.scoring.grade import grade_label
from main import get_config

_HORIZONS = [5, 20]
_WARMUP_DAYS = 280


def get_sp500_symbols() -> list[str]:
    tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    syms = [str(s).replace(".", "-").strip() for s in tables[0]["Symbol"].tolist()]
    return sorted(set(syms))


def fetch_all(symbols: list[str], years: int) -> dict[str, pd.DataFrame]:
    print(f"[SP500] Downloading {years}y history for {len(symbols)} symbols (batched)...")
    out: dict[str, pd.DataFrame] = {}
    batch = 100
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        raw = yf.download(chunk, period=f"{years}y", group_by="ticker",
                          auto_adjust=True, progress=False, threads=True)
        for sym in chunk:
            try:
                df = raw[sym].dropna(how="all") if len(chunk) > 1 else raw
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.dropna(subset=["Close"])
                if len(df) >= _WARMUP_DAYS:
                    out[sym] = df
            except Exception:
                pass
        print(f"[SP500] ...{min(i + batch, len(symbols))}/{len(symbols)} fetched, kept {len(out)}")
    return out


def run(years: int, step: int) -> list[dict]:
    watch = get_config().get("symbols", [])
    universe = sorted(set(get_sp500_symbols()) | set(watch))
    data = fetch_all(universe + ["SPY"], years)
    spy_df = data.pop("SPY", None)
    if spy_df is None or spy_df.empty:
        raise RuntimeError("SPY fetch failed")
    spy_close = spy_df["Close"].astype(float)
    spy_index = spy_df.index
    n_days = len(spy_index)

    records: list[dict] = []
    # Pre-locate each symbol's positional index by date for speed
    for day_i in range(_WARMUP_DAYS, n_days - max(_HORIZONS), step):
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

        spy_entry = float(spy_close.iloc[day_i])
        for sym, window in windows.items():
            price = float(window["Close"].astype(float).iloc[-1])
            if not us_market.liquidity_gate(window, price).get("passed"):
                continue
            v2_total, _ = us_market.score_v2(window, rs_pct.get(sym))
            grade = grade_label(v2_total)
            # keep the full cross-section only sparsely to bound memory:
            # always record S/A; sample the rest 1-in-3 by symbol hash
            if grade not in ("S", "A") and (hash(sym) + day_i) % 3:
                continue

            full = data[sym]
            entry_pos = full.index.get_loc(window.index[-1])
            if entry_pos + max(_HORIZONS) >= len(full):
                continue
            rec = {"date": str(signal_date.date())[:10], "symbol": sym,
                   "grade": grade, "v2": v2_total,
                   "weekly_up": us_market.weekly_direction_up(window),
                   "in_watchlist": sym in watch}
            for h in _HORIZONS:
                fwd = float(full["Close"].iloc[entry_pos + h]) / price - 1
                spy_fwd = float(spy_close.iloc[min(day_i + h, len(spy_close) - 1)]) / spy_entry - 1
                rec[f"ret_{h}d"] = round(fwd * 100, 2)
                rec[f"alpha_{h}d"] = round((fwd - spy_fwd) * 100, 2)
            records.append(rec)

        if (day_i - _WARMUP_DAYS) % (step * 20) == 0:
            print(f"[SP500] ...{str(signal_date.date())[:10]} cross-section={len(composites)} records={len(records)}")
    return records


def summarize(records: list[dict]) -> dict:
    df = pd.DataFrame(records)
    out: dict = {"n_obs": len(df), "n_dates": df["date"].nunique(),
                 "sampling_note": "non-S/A grades subsampled 1-in-3; bucket stats unaffected (within-bucket means)"}

    def bucket(sub: pd.DataFrame) -> dict:
        r5, a5 = sub["ret_5d"].dropna(), sub["alpha_5d"].dropna()
        r20, a20 = sub["ret_20d"].dropna(), sub["alpha_20d"].dropna()
        return {"n": len(sub),
                "win_5d": round(float((r5 > 0).mean() * 100), 1),
                "avg_alpha_5d": round(float(a5.mean()), 2),
                "win_20d": round(float((r20 > 0).mean() * 100), 1),
                "avg_ret_20d": round(float(r20.mean()), 2),
                "avg_alpha_20d": round(float(a20.mean()), 2)}

    out["by_grade"] = {g: bucket(grp) for g, grp in df.groupby("grade")}
    out["S_weekly_up"] = bucket(df[(df.grade == "S") & df.weekly_up])
    out["SA_weekly_up"] = bucket(df[df.grade.isin(["S", "A"]) & df.weekly_up])
    out["watchlist_only_S"] = bucket(df[(df.grade == "S") & df.in_watchlist])
    out["non_watchlist_S"] = bucket(df[(df.grade == "S") & ~df.in_watchlist])

    df["year"] = pd.to_datetime(df["date"]).dt.year
    by_year = {}
    for yr, g in df.groupby("year"):
        s = g[g.grade == "S"]["alpha_20d"].dropna()
        rest = g[~g.grade.isin(["S", "A"])]["alpha_20d"].dropna()
        by_year[str(yr)] = {
            "S_n": len(s), "S_alpha20": round(float(s.mean()), 2) if len(s) else None,
            "rest_alpha20": round(float(rest.mean()), 2) if len(rest) else None,
        }
    out["S_by_year"] = by_year
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--step", type=int, default=5)
    args = parser.parse_args()

    records = run(args.years, args.step)
    summary = summarize(records)
    out_path = _REPO_ROOT / "data" / f"backtest_score_v2_sp500_{args.years}y.json"
    out_path.write_text(json.dumps({
        "generated_at": str(date.today()),
        "years": args.years, "step_days": args.step,
        "universe": "current S&P 500 + watchlist (residual bias: not point-in-time constituents)",
        "summary": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[SP500] Written {out_path}")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("S_by_year",)},
                     ensure_ascii=False, indent=2))
