"""大回測30 — 30-year validation of the market-timing signals on INDEX data.

Why 30y here and only 10y for stock-level backtests: index data (SPY since
1993, QQQ since 1999) has zero survivorship/delisting/membership bias, and
regime-grade questions need many bear markets — this window holds dot-com
(2000-02), GFC (2008-09), 2011, 2015-16, 2018Q4, 2020 COVID, 2022. The
10y stock windows contain exactly one (2022).

Validates the PRODUCTION rules from src/indicators/market_timing.py as-is
(no re-tuning — this is out-of-sample-in-time validation, not a fit):
  1. Distribution-day risk zones -> conditional forward SPY 20/60d returns
     (does SEVERE actually predict worse outcomes?)
  2. FTD_CONFIRMED events -> event-study forward 20/60/120d returns + failure
     rate (close back below the swing low within 25 sessions)
  3. SPY>200MA regime -> conditional returns, and zone x regime interaction

Honesty notes baked in: daily conditional means use OVERLAPPING forward
windows (inflated n) — significance is therefore reported on non-overlapping
subsamples (every 20th/60th day). FTD events are discrete, no overlap issue.

Usage: python scripts/backtest_market_timing_30y.py [--years 30]
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

from src.indicators.market_timing import distribution_days, ftd_state, _fmt_date

_WARMUP = 300


def _fetch(symbol: str, years: int) -> pd.DataFrame:
    df = yf.download(symbol, period=f"{years}y", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Close"])


def _zone(count: int | None) -> str:
    if count is None:
        return "NA"
    if count >= 7:
        return "SEVERE"
    if count >= 5:
        return "HIGH"
    if count >= 3:
        return "CAUTION"
    return "NORMAL"


def run(years: int) -> tuple[pd.DataFrame, list[dict]]:
    spy = _fetch("SPY", years)
    qqq = _fetch("QQQ", years)
    print(f"[MT30] SPY {len(spy)} bars ({_fmt_date(spy.index[0])} .. {_fmt_date(spy.index[-1])}); "
          f"QQQ {len(qqq)} bars from {_fmt_date(qqq.index[0])}")
    close = spy["Close"].astype(float)
    n = len(spy)

    daily: list[dict] = []
    ftd_events: list[dict] = []
    last_ftd_date: str | None = None

    for i in range(_WARMUP, n - 60):
        window = spy.iloc[: i + 1]
        bar_date = _fmt_date(spy.index[i])

        dd_spy = distribution_days(window)["count"]
        # dual-index worst count (production rule) where QQQ exists
        dd_worst = dd_spy
        try:
            qpos = qqq.index.get_indexer([spy.index[i]], method="nearest")[0]
            if qpos >= 26 and abs((qqq.index[qpos] - spy.index[i]).days) <= 5:
                dd_q = distribution_days(qqq.iloc[: qpos + 1])["count"]
                if dd_q is not None and dd_spy is not None:
                    dd_worst = max(dd_spy, dd_q)
        except Exception:
            pass

        ftd = ftd_state(window)
        sma200 = float(close.iloc[max(0, i - 199): i + 1].mean())
        above_200 = float(close.iloc[i]) > sma200

        price = float(close.iloc[i])
        fwd20 = float(close.iloc[i + 20]) / price - 1
        fwd60 = float(close.iloc[i + 60]) / price - 1

        daily.append({
            "date": bar_date, "zone_spy": _zone(dd_spy), "zone_dual": _zone(dd_worst),
            "ftd_state": ftd.get("state"), "above_200ma": above_200,
            "fwd20": fwd20 * 100, "fwd60": fwd60 * 100,
        })

        # FTD event: state machine just stamped a NEW confirmation date == today
        if ftd.get("state") == "FTD_CONFIRMED" and ftd.get("ftd_date") == bar_date \
                and ftd.get("ftd_date") != last_ftd_date:
            last_ftd_date = ftd["ftd_date"]
            ev = {"date": bar_date, "decline_pct": ftd.get("decline_pct"),
                  "swing_low_date": ftd.get("swing_low_date")}
            for h in (20, 60, 120):
                ev[f"fwd{h}"] = round((float(close.iloc[min(i + h, n - 1)]) / price - 1) * 100, 2)
            # failure: close undercuts the swing low within 25 sessions
            try:
                low_pos = spy.index.get_indexer([pd.Timestamp(ftd["swing_low_date"])], method="nearest")[0]
                swing_low_close = float(close.iloc[low_pos])
                post = close.iloc[i + 1: i + 26]
                ev["failed"] = bool((post < swing_low_close).any())
            except Exception:
                ev["failed"] = None
            ftd_events.append(ev)

        if (i - _WARMUP) % 1000 == 0:
            print(f"[MT30] ...{bar_date}")

    return pd.DataFrame(daily), ftd_events


def summarize(df: pd.DataFrame, events: list[dict]) -> dict:
    out: dict = {"n_days": len(df),
                 "window": f"{df['date'].iloc[0]} .. {df['date'].iloc[-1]}"}

    def cond(sub: pd.DataFrame, full: pd.DataFrame) -> dict:
        # significance from non-overlapping subsamples
        sub20 = sub.iloc[::20]["fwd20"]
        base20 = full.iloc[::20]["fwd20"]
        t = None
        if len(sub20) > 5 and sub20.std() > 0:
            t = round(float((sub20.mean() - base20.mean())
                            / (sub20.std() / np.sqrt(len(sub20)))), 2)
        return {"pct_of_days": round(len(sub) / len(full) * 100, 1),
                "avg_fwd20": round(float(sub["fwd20"].mean()), 2),
                "win20": round(float((sub["fwd20"] > 0).mean() * 100), 1),
                "avg_fwd60": round(float(sub["fwd60"].mean()), 2),
                "win60": round(float((sub["fwd60"] > 0).mean() * 100), 1),
                "t_vs_all(nonoverlap)": t}

    out["baseline_all_days"] = {"avg_fwd20": round(float(df["fwd20"].mean()), 2),
                                "avg_fwd60": round(float(df["fwd60"].mean()), 2),
                                "win20": round(float((df["fwd20"] > 0).mean() * 100), 1)}
    out["by_zone_spy"] = {z: cond(g, df) for z, g in df.groupby("zone_spy")}
    dual = df[df["zone_dual"] != "NA"]
    out["by_zone_dual_since1999"] = {z: cond(g, dual) for z, g in dual.groupby("zone_dual")}
    out["by_regime_200ma"] = {("above" if k else "below"): cond(g, df)
                              for k, g in df.groupby("above_200ma")}
    # interaction: the actionable cell is SEVERE while still above 200MA
    for zone in ("SEVERE", "HIGH"):
        sub = df[(df.zone_spy == zone) & df.above_200ma]
        if len(sub) > 40:
            out[f"{zone}_while_above200ma"] = cond(sub, df)

    df["decade"] = df["date"].str[:3] + "0s"
    out["severe_by_decade"] = {
        d: {"n": int((g.zone_spy == "SEVERE").sum()),
            "severe_fwd20": round(float(g[g.zone_spy == "SEVERE"]["fwd20"].mean()), 2)
            if (g.zone_spy == "SEVERE").any() else None,
            "all_fwd20": round(float(g["fwd20"].mean()), 2)}
        for d, g in df.groupby("decade")
    }

    ev = pd.DataFrame(events)
    if len(ev):
        out["ftd_events"] = {
            "n_events": len(ev),
            "failure_rate_pct": round(float(ev["failed"].dropna().mean() * 100), 1),
            "avg_fwd20": round(float(ev["fwd20"].mean()), 2),
            "win20": round(float((ev["fwd20"] > 0).mean() * 100), 1),
            "avg_fwd60": round(float(ev["fwd60"].mean()), 2),
            "win60": round(float((ev["fwd60"] > 0).mean() * 100), 1),
            "avg_fwd120": round(float(ev["fwd120"].mean()), 2),
            "win120": round(float((ev["fwd120"] > 0).mean() * 100), 1),
            "events_after_10pct_declines": ev[ev["decline_pct"] >= 10].to_dict("records"),
        }
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=30)
    args = parser.parse_args()

    df, events = run(args.years)
    summary = summarize(df, events)
    out_path = _REPO_ROOT / "data" / f"backtest_market_timing_{args.years}y.json"
    out_path.write_text(json.dumps({
        "generated_at": str(date.today()),
        "years": args.years,
        "note": "index-level (SPY/QQQ) — zero survivorship/membership bias; production "
                "rules validated as-is, no re-tuning; overlapping-window caveat handled "
                "via non-overlapping t-stats",
        "summary": summary,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[MT30] Written {out_path}")
    print(json.dumps({k: v for k, v in summary.items()
                      if k in ("baseline_all_days", "by_zone_spy", "ftd_events")},
                     ensure_ascii=False, indent=2, default=str))
