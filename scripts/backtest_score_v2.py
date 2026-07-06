"""10y walk-forward comparison: current technical score (v1) vs proposed
composite score v2 — measures cross-sectional ranking power (IC) and
grade-bucket forward returns.

Motivation (2026-07 scoring audit): the live 100-pt score tops out at C grade
structurally — single-day volume surge (6pts) is a lottery, RSI>70 zeroes out
the strongest momentum names, market sentiment (10pts) is identical for every
stock so it compresses the distribution without ranking anything, and the
insider-flow points are unreachable for large caps. Score v2 keeps only
point-in-time reconstructable price/volume factors:

    RS rating (cross-sectional 6m + 12m-skip-1 percentile) .. 40 pts
    Minervini trend template pass count (0-8) ............... 30 pts
    52-week-high proximity .................................. 15 pts
    Volume accumulation (50d up/down volume ratio +
        volatility contraction near highs) .................. 15 pts

Market sentiment is deliberately EXCLUDED from the per-stock score (it becomes
a regime gate, tagged on every record for the by-regime breakdown instead).

Fundamental/news/social/Form4 factors are NOT reconstructable historically
(yfinance only exposes current snapshots) — including them would leak today's
data into decade-old decisions. The comparison is therefore v1 TECHNICAL score
(0-30, the reconstructable part of the live score) vs score v2, on identical
days/symbols. Ranking power (Spearman IC) is scale-invariant so the different
maxima don't distort the comparison.

KNOWN BIAS (same as backtest_shadow_strategy.py): universe = today's curated
40-symbol watchlist -> survivorship-optimistic in absolute terms. It hits v1
and v2 equally, so the RELATIVE comparison stays informative.

Sampling: every 5th trading day (weekly) to reduce overlapping-horizon
autocorrelation; forward horizons 5/10/20d.

Usage: python scripts/backtest_score_v2.py [--years 10] [--step 5]
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
from src.indicators.technical import technical_score
from src.scoring.grade import grade_label
from main import get_config

_HORIZONS = [5, 10, 20]
_WARMUP_DAYS = 280  # 252 for 52w + 12m-skip-1 momentum needs 273; round up


def _fetch_all(symbols: list[str], years: int) -> dict[str, pd.DataFrame]:
    print(f"[ScoreV2] Downloading {years}y history for {len(symbols)} symbols...")
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
            print(f"[ScoreV2] {sym}: fetch failed ({e})")
            out[sym] = pd.DataFrame()
    return out


def _composite_return(close: pd.Series) -> float | None:
    """0.5*6m + 0.5*(12m skip last month), at the window's last bar."""
    i = len(close) - 1
    parts = []
    if i >= 126:
        parts.append(float(close.iloc[i] / close.iloc[i - 126] - 1))
    if i >= 252 + 21:
        parts.append(float(close.iloc[i - 21] / close.iloc[i - 252 - 21] - 1))
    return sum(parts) / len(parts) if parts else None


def score_v2(window: pd.DataFrame, rs_rating: int | None) -> tuple[int, dict[str, int]]:
    """Composite 0-100 from point-in-time price/volume only."""
    close = window["Close"].astype(float)
    high = window["High"].astype(float)
    volume = window["Volume"].astype(float)
    price = float(close.iloc[-1])
    parts: dict[str, int] = {}

    # 1) RS rating percentile -> 40
    parts["rs"] = int(round((rs_rating or 0) * 0.40))

    # 2) Minervini trend template -> 30
    mt = us_market.minervini_trend_template(window, rs_rating=rs_rating)
    parts["trend"] = int(round(mt["pass_count"] / 8 * 30))

    # 3) 52w-high proximity -> 15
    hi_52w = float(high.tail(252).max()) if len(high) >= 252 else float(high.max())
    pct_from_high = (price - hi_52w) / hi_52w * 100 if hi_52w > 0 else -99
    if pct_from_high >= -5:
        parts["high52"] = 15
    elif pct_from_high >= -15:
        parts["high52"] = 10
    elif pct_from_high >= -25:
        parts["high52"] = 5
    else:
        parts["high52"] = 0

    # 4) Volume accumulation -> 15
    #    a) 50d up-day vs down-day volume ratio (institutional accumulation)
    acc = 0
    chg = close.diff()
    v50 = volume.tail(50)
    c50 = chg.tail(50)
    up_vol = float(v50[c50 > 0].sum())
    dn_vol = float(v50[c50 < 0].sum())
    if dn_vol > 0:
        ratio = up_vol / dn_vol
        if ratio >= 1.5:
            acc += 8
        elif ratio >= 1.2:
            acc += 5
        elif ratio >= 1.0:
            acc += 2
    #    b) volatility contraction while holding near highs (VCP-flavored)
    if len(close) >= 40 and pct_from_high >= -15:
        vol_now = float(close.tail(20).std())
        vol_prior = float(close.iloc[-40:-20].std())
        if vol_prior > 0:
            cr = vol_now / vol_prior
            if cr < 0.8:
                acc += 7
            elif cr < 1.0:
                acc += 3
    parts["volume"] = min(acc, 15)

    total = parts["rs"] + parts["trend"] + parts["high52"] + parts["volume"]
    return min(total, 100), parts


def run(years: int, step: int) -> dict:
    cfg = get_config()
    symbols: list[str] = cfg.get("symbols", [])
    data = _fetch_all(symbols + ["SPY"], years)
    spy_df = data.get("SPY")
    if spy_df is None or spy_df.empty:
        raise RuntimeError("SPY fetch failed")
    spy_close = spy_df["Close"].astype(float)
    spy_index = spy_df.index
    n_days = len(spy_index)

    records: list[dict] = []
    sample_days = range(_WARMUP_DAYS, n_days - max(_HORIZONS), step)
    print(f"[ScoreV2] Sampling {len(list(sample_days))} dates (every {step} trading days)...")

    for day_i in range(_WARMUP_DAYS, n_days - max(_HORIZONS), step):
        signal_date = spy_index[day_i]

        # Pass 1: point-in-time windows + cross-sectional RS percentile
        windows: dict[str, pd.DataFrame] = {}
        composites: dict[str, float] = {}
        for sym in symbols:
            df = data.get(sym)
            if df is None or df.empty:
                continue
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
            c = _composite_return(window["Close"].astype(float))
            if c is not None:
                composites[sym] = c
        if len(composites) < 8:  # need enough names for a meaningful cross-section
            continue
        rs_pct = us_market.rs_percentile(composites)

        # Regime tag (context only, not in score)
        spy_window = spy_close.iloc[: day_i + 1]
        mt_flags = {s: us_market.minervini_trend_template(w, rs_rating=rs_pct.get(s))
                    for s, w in windows.items()}
        breadth = round(sum(1 for m in mt_flags.values() if m.get("phase2")) / len(mt_flags) * 100, 1)
        regime = us_market.market_regime(spy_window.to_frame("Close"), breadth)["regime"]

        # Pass 2: score both mechanisms, record forward returns
        spy_window_df = spy_df.iloc[: day_i + 1]
        for sym, window in windows.items():
            price = float(window["Close"].astype(float).iloc[-1])
            if not us_market.liquidity_gate(window, price).get("passed"):
                continue

            v1_score, _ = technical_score(window, spy_window_df)
            v2_total, v2_parts = score_v2(window, rs_pct.get(sym))

            full_df = data[sym]
            entry_pos = full_df.index.get_loc(window.index[-1])
            rec: dict = {
                "date": str(signal_date.date()),
                "symbol": sym,
                "regime": regime,
                "v1_tech": v1_score,
                "v2": v2_total,
                "v2_grade": grade_label(v2_total),
                "rs_rating": rs_pct.get(sym),
            }
            ok = True
            for h in _HORIZONS:
                if entry_pos + h >= len(full_df) or day_i + h >= len(spy_close):
                    ok = False
                    break
                fwd = float(full_df["Close"].iloc[entry_pos + h]) / price - 1
                spy_fwd = float(spy_close.iloc[day_i + h]) / float(spy_close.iloc[day_i]) - 1
                rec[f"ret_{h}d"] = round(fwd * 100, 2)
                rec[f"alpha_{h}d"] = round((fwd - spy_fwd) * 100, 2)
            if ok:
                records.append(rec)

        if (day_i - _WARMUP_DAYS) % (step * 50) == 0:
            print(f"[ScoreV2] ...{signal_date.date()}")

    return {"records": records}


def _ic_series(df: pd.DataFrame, score_col: str, ret_col: str) -> pd.Series:
    """Per-date cross-sectional Spearman IC."""
    out = {}
    for dt, g in df.groupby("date"):
        if len(g) >= 8 and g[score_col].nunique() > 1:
            out[dt] = g[score_col].rank().corr(g[ret_col].rank())
    return pd.Series(out).dropna()


def summarize(records: list[dict]) -> dict:
    df = pd.DataFrame(records)
    out: dict = {"n_obs": len(df), "n_dates": df["date"].nunique()}

    # 1) Ranking power: mean IC per mechanism/horizon
    ics: dict = {}
    for col, name in [("v1_tech", "v1_technical"), ("v2", "v2")]:
        for h in _HORIZONS:
            s = _ic_series(df, col, f"ret_{h}d")
            ics[f"{name}_ic_{h}d"] = {
                "mean": round(float(s.mean()), 4),
                "std": round(float(s.std()), 4),
                "pct_positive": round(float((s > 0).mean() * 100), 1),
                "n_dates": len(s),
                "t_stat": round(float(s.mean() / (s.std() / np.sqrt(len(s)))), 2) if len(s) > 1 and s.std() > 0 else None,
            }
    out["ic"] = ics

    # IC by year (20d horizon) — time robustness
    df["year"] = pd.to_datetime(df["date"]).dt.year
    by_year: dict = {}
    for yr, g in df.groupby("year"):
        row = {"n_dates": g["date"].nunique()}
        for col, name in [("v1_tech", "v1"), ("v2", "v2")]:
            s = _ic_series(g, col, "ret_20d")
            row[f"{name}_ic_20d"] = round(float(s.mean()), 4) if len(s) else None
        by_year[str(yr)] = row
    out["ic_by_year_20d"] = by_year

    # 2) v2 grade buckets — the "does A beat C" table
    buckets: dict = {}
    for grade, g in df.groupby("v2_grade"):
        b = {"n": len(g), "pct_of_obs": round(len(g) / len(df) * 100, 1)}
        for h in _HORIZONS:
            r = g[f"ret_{h}d"].dropna()
            a = g[f"alpha_{h}d"].dropna()
            b[f"win_{h}d"] = round(float((r > 0).mean() * 100), 1)
            b[f"avg_ret_{h}d"] = round(float(r.mean()), 2)
            b[f"avg_alpha_{h}d"] = round(float(a.mean()), 2)
        buckets[grade] = b
    out["v2_grade_buckets"] = buckets

    # 3) v1 technical quartiles (its scale can't reach letter grades honestly)
    df["v1_q"] = pd.qcut(df["v1_tech"], 4, labels=["Q1_low", "Q2", "Q3", "Q4_high"], duplicates="drop")
    v1_buckets: dict = {}
    for q, g in df.groupby("v1_q", observed=True):
        b = {"n": len(g)}
        for h in _HORIZONS:
            r = g[f"ret_{h}d"].dropna()
            a = g[f"alpha_{h}d"].dropna()
            b[f"win_{h}d"] = round(float((r > 0).mean() * 100), 1)
            b[f"avg_ret_{h}d"] = round(float(r.mean()), 2)
            b[f"avg_alpha_{h}d"] = round(float(a.mean()), 2)
        v1_buckets[str(q)] = b
    out["v1_tech_quartiles"] = v1_buckets

    # 4) v2 grade x regime (20d alpha) — regime robustness
    by_regime: dict = {}
    for (regime, grade), g in df.groupby(["regime", "v2_grade"]):
        a = g["alpha_20d"].dropna()
        if len(a) >= 20:
            by_regime.setdefault(regime, {})[grade] = {
                "n": len(a),
                "avg_alpha_20d": round(float(a.mean()), 2),
                "alpha_win_20d": round(float((a > 0).mean() * 100), 1),
            }
    out["v2_by_regime"] = by_regime

    # 5) Threshold sensitivity: v2 decile monotonicity (20d alpha)
    df["v2_decile"] = pd.qcut(df["v2"], 10, labels=False, duplicates="drop")
    deciles = {}
    for d, g in df.groupby("v2_decile"):
        a = g["alpha_20d"].dropna()
        deciles[f"d{int(d)}"] = {"n": len(a), "avg_alpha_20d": round(float(a.mean()), 2),
                                 "score_range": f"{int(g['v2'].min())}-{int(g['v2'].max())}"}
    out["v2_deciles_20d_alpha"] = deciles

    # 5b) Top-grade time robustness: does S/A edge rely on 1-2 exceptional years?
    top_by_year: dict = {}
    for yr, g in df.groupby("year"):
        row: dict = {}
        for grade in ("S", "A"):
            a = g.loc[g["v2_grade"] == grade, "alpha_20d"].dropna()
            if len(a) >= 10:
                row[grade] = {"n": len(a), "avg_alpha_20d": round(float(a.mean()), 2),
                              "alpha_win_20d": round(float((a > 0).mean() * 100), 1)}
        rest = g.loc[~g["v2_grade"].isin(["S", "A"]), "alpha_20d"].dropna()
        if len(rest):
            row["rest_avg_alpha_20d"] = round(float(rest.mean()), 2)
        top_by_year[str(yr)] = row
    out["v2_top_grades_by_year"] = top_by_year

    # 6) v2 grade distribution (does the ceiling open up?)
    out["v2_grade_distribution_pct"] = {
        g: round(c / len(df) * 100, 1) for g, c in df["v2_grade"].value_counts().items()
    }
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--step", type=int, default=5)
    args = parser.parse_args()

    raw = run(args.years, args.step)
    summary = summarize(raw["records"])

    out_path = _REPO_ROOT / "data" / f"backtest_score_v2_{args.years}y.json"
    out_path.write_text(json.dumps({
        "generated_at": str(date.today()),
        "years": args.years,
        "step_days": args.step,
        "universe_size": len(get_config().get("symbols", [])),
        "known_bias": "current watchlist as historical universe (survivorship-optimistic); "
                      "hits v1 and v2 equally so the relative comparison is valid",
        "summary": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[ScoreV2] Written {out_path}")
    print(json.dumps(summary["ic"], ensure_ascii=False, indent=2))
