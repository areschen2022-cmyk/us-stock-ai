"""Historical (5-10y) backtest of the price-only shadow strategy signals.

Only backtests what's actually reconstructable from historical OHLCV:
- 'shadow'          : RS>=80 (cross-sectional percentile) AND Minervini
                       phase2 AND liquidity gate — same rule as
                       main.py::_log_validation_signals's 'shadow' group.
- 'potential_radar'  : low_base / early_strength VCP stages (same rule as
                       us_market.potential_radar_stage), on symbols that are
                       NOT already phase2.

Fundamental/news/social/Form4 factors are NOT included — yfinance's `info`
dict only exposes CURRENT fundamentals (no historical snapshot), so the full
100-pt live score and research_rank cannot be reconstructed point-in-time.
Reusing them here would silently leak today's data into a "10 years ago"
decision (look-ahead bias), so they're deliberately left out rather than
faked.

KNOWN BIAS: this uses config.yaml's CURRENT 40-symbol watchlist as the
backtest universe. Several of these stocks (PLTR IPO 2020, COIN 2021, ARM
2023, MSTR's bitcoin-proxy pivot ~2020, SMCI's AI-boom relevance) didn't
exist or didn't look like today's "strong" set for most of a 10-year window.
The watchlist itself was curated BECAUSE these are today's strong movers, so
results are optimistic relative to what a strategy blindly run in 2016 would
have produced with no foreknowledge of which stocks would matter later. This
was a deliberate scope trade-off (see chat) — not a hidden gap.

Usage: python scripts/backtest_shadow_strategy.py [--years 10]
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
from main import get_config

_HORIZONS = [3, 5, 10, 20]
_WARMUP_DAYS = 260  # need >=252 for 52w hi/lo + 200MA before the first signal day


def _fetch_all(symbols: list[str], years: int) -> dict[str, pd.DataFrame]:
    print(f"[Backtest] Downloading {years}y history for {len(symbols)} symbols...")
    raw = yf.download(symbols, period=f"{years}y", group_by="ticker",
                       auto_adjust=True, progress=False, threads=True)
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = raw[sym].dropna(how="all") if len(symbols) > 1 else raw
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            out[sym] = df.dropna(subset=["Close"])
        except Exception as e:
            print(f"[Backtest] {sym}: fetch failed ({e})")
            out[sym] = pd.DataFrame()
    return out


def _composite_return(close: pd.Series, i: int) -> float | None:
    """6m + 12m-skip-1 composite momentum, evaluated as of index i (matches
    us_market.rs_rating_63d's inputs but computed point-in-time for the
    walk-forward loop instead of only at the latest bar)."""
    parts = []
    if i >= 126:
        parts.append(float(close.iloc[i] / close.iloc[i - 126] - 1))
    if i >= 252 + 21:
        parts.append(float(close.iloc[i - 21] / close.iloc[i - 252 - 21] - 1))
    return sum(parts) / len(parts) if parts else None


def run_backtest(years: int = 10) -> dict:
    cfg = get_config()
    symbols: list[str] = cfg.get("symbols", [])
    universe = symbols + ["SPY"]
    data = _fetch_all(universe, years)
    spy_df = data.get("SPY")
    if spy_df is None or spy_df.empty:
        raise RuntimeError("SPY fetch failed — cannot compute RS/alpha")
    spy_close = spy_df["Close"].astype(float)
    spy_index = spy_df.index

    results: dict[str, list[dict]] = {"shadow": [], "potential_radar": []}
    n_days = len(spy_index)
    print(f"[Backtest] Walking {n_days - _WARMUP_DAYS} trading days across {len(symbols)} symbols...")

    for day_i in range(_WARMUP_DAYS, n_days - max(_HORIZONS)):
        signal_date = spy_index[day_i]

        # Pass 1: cross-sectional composite momentum -> RS percentile
        composites: dict[str, float] = {}
        windows: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            df = data.get(sym)
            if df is None or df.empty:
                continue
            # align by date position; skip if this symbol has no data yet
            # (post-IPO gap) or has stopped trading (delisted) by this date
            try:
                pos = df.index.get_indexer([signal_date], method="nearest")[0]
            except Exception:
                continue
            if pos < 0 or abs((df.index[pos] - signal_date).days) > 5:
                continue
            window = df.iloc[: pos + 1]
            if len(window) < _WARMUP_DAYS:
                continue
            windows[sym] = window
            c = _composite_return(window["Close"].astype(float), len(window) - 1)
            if c is not None:
                composites[sym] = c

        if not composites:
            continue
        rs_pct = us_market.rs_percentile(composites)

        # Pass 2: Minervini + liquidity + potential-radar per symbol
        spy_window = spy_close.iloc[: day_i + 1]
        for sym, window in windows.items():
            rating = rs_pct.get(sym)
            mt = us_market.minervini_trend_template(window, rs_rating=rating)
            price = float(window["Close"].iloc[-1])
            liq = us_market.liquidity_gate(window, price)
            if not liq.get("passed"):
                continue

            entry_idx_in_full = data[sym].index.get_loc(window.index[-1])
            full_df = data[sym]

            def _fwd_return(h: int) -> float | None:
                if entry_idx_in_full + h >= len(full_df):
                    return None
                exit_price = float(full_df["Close"].iloc[entry_idx_in_full + h])
                return round((exit_price - price) / price * 100, 2)

            def _spy_fwd_return(h: int) -> float | None:
                if day_i + h >= len(spy_close):
                    return None
                spy_exit = float(spy_close.iloc[day_i + h])
                spy_entry = float(spy_close.iloc[day_i])
                return round((spy_exit - spy_entry) / spy_entry * 100, 2)

            record = {
                "symbol": sym,
                "date": str(window.index[-1].date()) if hasattr(window.index[-1], "date") else str(window.index[-1]),
                "rs_rating": rating,
            }
            for h in _HORIZONS:
                record[f"return_{h}d"] = _fwd_return(h)
                spy_r = _spy_fwd_return(h)
                if record[f"return_{h}d"] is not None and spy_r is not None:
                    record[f"alpha_{h}d"] = round(record[f"return_{h}d"] - spy_r, 2)

            if (rating or 0) >= 80 and mt.get("phase2"):
                results["shadow"].append(record)
            else:
                pot = us_market.potential_radar_stage(window, price, mt.get("phase2", False))
                if pot.get("stage") in ("low_base", "early_strength"):
                    results["potential_radar"].append({**record, "stage": pot["stage"]})

        if day_i % 250 == 0:
            print(f"[Backtest] ...{spy_index[day_i].date()} ({day_i - _WARMUP_DAYS}/{n_days - _WARMUP_DAYS - max(_HORIZONS)})")

    return results


def _summarize(records: list[dict]) -> dict:
    if not records:
        return {"n": 0}
    df = pd.DataFrame(records)
    out: dict = {"n": len(df)}
    for h in _HORIZONS:
        col = f"return_{h}d"
        vals = df[col].dropna()
        if vals.empty:
            continue
        win = (vals > 0).sum()
        out[f"n_{h}d"] = len(vals)
        out[f"win_rate_{h}d"] = round(win / len(vals) * 100, 1)
        out[f"avg_return_{h}d"] = round(vals.mean(), 2)
        acol = f"alpha_{h}d"
        if acol in df.columns:
            avals = df[acol].dropna()
            if not avals.empty:
                out[f"avg_alpha_{h}d"] = round(avals.mean(), 2)
                out[f"alpha_win_rate_{h}d"] = round((avals > 0).sum() / len(avals) * 100, 1)
    # by-year breakdown for regime robustness (5d horizon only, keeps it readable)
    if "date" in df.columns:
        df["year"] = pd.to_datetime(df["date"]).dt.year
        by_year = {}
        for yr, g in df.groupby("year"):
            v = g["return_5d"].dropna()
            if len(v):
                by_year[str(yr)] = {"n": len(v), "win_rate": round((v > 0).sum() / len(v) * 100, 1), "avg_return": round(v.mean(), 2)}
        out["by_year"] = by_year
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=10)
    args = parser.parse_args()

    raw_results = run_backtest(years=args.years)
    summary = {grp: _summarize(recs) for grp, recs in raw_results.items()}

    out_path = _REPO_ROOT / "data" / f"backtest_shadow_{args.years}y.json"
    out_path.write_text(json.dumps({
        "generated_at": str(date.today()),
        "years": args.years,
        "universe_size": len(get_config().get("symbols", [])),
        "known_bias": "current watchlist used as historical universe — survivorship-biased, see script docstring",
        "summary": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[Backtest] Written {out_path}")
    for grp, s in summary.items():
        print(f"\n=== {grp} ===")
        print(json.dumps(s, ensure_ascii=False, indent=2))
