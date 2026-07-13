"""P1 backtests: (A) weekly-direction + extension filters on v2 S/A signals,
(B) exit-rule sweep on v2 S-grade entries.

Part A ports two validated lessons onto US data:
- XAUUSD D1/H4 bias filter (ares_kp_20eb990b526239): higher-timeframe
  direction should FILTER lower-timeframe entries. US analog tested here:
  weekly trend up = price > 50d MA AND 50d MA rising vs 5 bars ago.
- tw-stock-ai 進場條件保護 (kp_tw_entry_trigger_protection): don't chase
  extended names. US analog: extended = close > 8% above its 20d MA.

Part B ports the "fix exits before entries" conclusion
(ares_kp_f85cb1c84f5eb2 exit-RR plateau + ares_kp_167026341a06c7):
same entries (v2 S-grade), different exits:
  E0 hold20   : sell at close of bar 20 (current de facto behavior)
  E1 stop     : protective stop only (max(2ATR, 20d swing-low*0.98)), else bar-20 exit
  E2 stop+t3  : stop + target at entry+3*ATR
  E3 stop+t5  : stop + target at entry+5*ATR  (XAUUSD found RR search should stop ~5)
  E4 stop+ma20: stop + trail-exit on first close below 20d MA (max 40 bars)
  E5 stop+time10: stop + time-stop at bar 10
Same-bar stop&target conflict resolves to STOP (pessimistic). Gap through
stop fills at the open (realistic). No commissions (relative comparison of
variants on identical entries; costs shift all variants equally).

KNOWN BIAS: same curated-watchlist universe as backtest_score_v2.py —
absolute numbers optimistic, relative comparisons valid.

Usage: python scripts/backtest_p1_filters_exits.py [--years 10] [--step 5]
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
from scripts.backtest_score_v2 import _fetch_all, _composite_return, score_v2
from main import get_config

_WARMUP_DAYS = 280
_MAX_HOLD = 40  # bars, cap for trailing exits


def _atr(df: pd.DataFrame, n: int = 14) -> float:
    high, low, close = (df[c].astype(float) for c in ("High", "Low", "Close"))
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return float(tr.rolling(n).mean().iloc[-1])


def _simulate_exit(full: pd.DataFrame, entry_pos: int, entry: float, stop: float,
                   target: float | None, ma_trail: bool, time_stop: int | None) -> dict | None:
    """Walk bars after entry_pos; return {ret_pct, bars_held, exit_kind}."""
    close = full["Close"].astype(float)
    n = len(full)
    last = min(entry_pos + _MAX_HOLD, n - 1)
    if entry_pos + 1 > last:
        return None
    for i in range(entry_pos + 1, last + 1):
        o = float(full["Open"].iloc[i]) if "Open" in full.columns else float(close.iloc[i])
        hi = float(full["High"].iloc[i])
        lo = float(full["Low"].iloc[i])
        bars = i - entry_pos
        # stop first (pessimistic on same-bar conflicts); gap fills at open
        if stop is not None and lo <= stop:
            fill = min(o, stop) if o < stop else stop
            return {"ret_pct": (fill / entry - 1) * 100, "bars": bars, "kind": "stop"}
        if target is not None and hi >= target:
            fill = max(o, target) if o > target else target
            return {"ret_pct": (fill / entry - 1) * 100, "bars": bars, "kind": "target"}
        if ma_trail and i >= 20:
            sma20 = float(close.iloc[i - 19: i + 1].mean())
            if float(close.iloc[i]) < sma20:
                return {"ret_pct": (float(close.iloc[i]) / entry - 1) * 100, "bars": bars, "kind": "ma_trail"}
        if time_stop is not None and bars >= time_stop:
            return {"ret_pct": (float(close.iloc[i]) / entry - 1) * 100, "bars": bars, "kind": "time"}
    # cap reached without trigger → exit at last close
    return {"ret_pct": (float(close.iloc[last]) / entry - 1) * 100, "bars": last - entry_pos, "kind": "cap"}


def run(years: int, step: int) -> tuple[list[dict], list[dict]]:
    cfg = get_config()
    symbols: list[str] = cfg.get("symbols", [])
    data = _fetch_all(symbols + ["SPY"], years)
    spy_df = data.get("SPY")
    if spy_df is None or spy_df.empty:
        raise RuntimeError("SPY fetch failed")
    spy_close = spy_df["Close"].astype(float)
    spy_index = spy_df.index
    n_days = len(spy_index)

    filter_records: list[dict] = []
    exit_trades: list[dict] = []

    for day_i in range(_WARMUP_DAYS, n_days - 21, step):
        signal_date = spy_index[day_i]
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
        if len(composites) < 8:
            continue
        rs_pct = us_market.rs_percentile(composites)

        for sym, window in windows.items():
            close = window["Close"].astype(float)
            price = float(close.iloc[-1])
            if not us_market.liquidity_gate(window, price).get("passed"):
                continue
            v2_total, _ = score_v2(window, rs_pct.get(sym))
            grade = grade_label(v2_total)
            if grade not in ("S", "A"):
                continue

            full = data[sym]
            entry_pos = full.index.get_loc(window.index[-1])
            if entry_pos + 21 >= len(full):
                continue

            # ---- Part A: filter flags + forward returns
            sma20 = float(close.tail(20).mean())
            sma50 = float(close.tail(50).mean())
            sma50_prev = float(close.iloc[-55:-5].tail(50).mean()) if len(close) >= 60 else sma50
            weekly_up = price > sma50 and sma50 > sma50_prev
            extended = price > sma20 * 1.08

            rec = {"date": str(signal_date.date()), "symbol": sym, "grade": grade,
                   "weekly_up": weekly_up, "extended": extended}
            spy_entry = float(spy_close.iloc[day_i])
            for h in (5, 10, 20):
                fwd = float(full["Close"].iloc[entry_pos + h]) / price - 1
                spy_fwd = float(spy_close.iloc[min(day_i + h, len(spy_close) - 1)]) / spy_entry - 1
                rec[f"ret_{h}d"] = round(fwd * 100, 2)
                rec[f"alpha_{h}d"] = round((fwd - spy_fwd) * 100, 2)
            filter_records.append(rec)

            # ---- Part B: exit sweep on S-grade only
            if grade != "S":
                continue
            atr = _atr(window)
            swing_stop = float(window["Low"].astype(float).tail(20).min()) * 0.98
            stop = max(price - 2 * atr, swing_stop)
            variants = {
                "E0_hold20":   dict(stop=None, target=None, ma_trail=False, time_stop=20),
                "E1_stop":     dict(stop=stop, target=None, ma_trail=False, time_stop=20),
                "E2_stop_t3":  dict(stop=stop, target=price + 3 * atr, ma_trail=False, time_stop=20),
                "E3_stop_t5":  dict(stop=stop, target=price + 5 * atr, ma_trail=False, time_stop=20),
                "E4_stop_ma20": dict(stop=stop, target=None, ma_trail=True, time_stop=None),
                "E5_stop_time10": dict(stop=stop, target=None, ma_trail=False, time_stop=10),
            }
            for name, kw in variants.items():
                r = _simulate_exit(full, entry_pos, price, kw["stop"], kw["target"],
                                   kw["ma_trail"], kw["time_stop"])
                if r:
                    exit_trades.append({"date": str(signal_date.date()), "symbol": sym,
                                        "variant": name, **r})

        if (day_i - _WARMUP_DAYS) % (step * 50) == 0:
            print(f"[P1] ...{signal_date.date()}")

    return filter_records, exit_trades


def summarize_filters(records: list[dict]) -> dict:
    df = pd.DataFrame(records)
    if df.empty:
        return {}

    def bucket(sub: pd.DataFrame) -> dict:
        out = {"n": len(sub)}
        for h in (5, 10, 20):
            a = sub[f"alpha_{h}d"].dropna()
            r = sub[f"ret_{h}d"].dropna()
            out[f"avg_alpha_{h}d"] = round(float(a.mean()), 2)
            out[f"win_{h}d"] = round(float((r > 0).mean() * 100), 1)
        return out

    res = {
        "baseline_SA": bucket(df),
        "weekly_up_only": bucket(df[df.weekly_up]),
        "weekly_down_excluded_pct": round(float((~df.weekly_up).mean() * 100), 1),
        "not_extended_only": bucket(df[~df.extended]),
        "extended_excluded_pct": round(float(df.extended.mean() * 100), 1),
        "extended_only(the_chase_bucket)": bucket(df[df.extended]),
        "weekly_up_and_not_extended": bucket(df[df.weekly_up & ~df.extended]),
    }
    # yearly stability of the combined filter vs baseline (20d alpha)
    df["year"] = pd.to_datetime(df["date"]).dt.year
    by_year = {}
    for yr, g in df.groupby("year"):
        both = g[g.weekly_up & ~g.extended]["alpha_20d"].dropna()
        base = g["alpha_20d"].dropna()
        by_year[str(yr)] = {
            "base_n": len(base), "base_alpha20": round(float(base.mean()), 2) if len(base) else None,
            "filt_n": len(both), "filt_alpha20": round(float(both.mean()), 2) if len(both) else None,
        }
    res["by_year"] = by_year
    return res


def summarize_exits(trades: list[dict]) -> dict:
    df = pd.DataFrame(trades)
    if df.empty:
        return {}
    out = {}
    for name, g in df.groupby("variant"):
        r = g["ret_pct"]
        gross_win = float(r[r > 0].sum())
        gross_loss = float(-r[r <= 0].sum())
        out[name] = {
            "n": len(g),
            "avg_ret_pct": round(float(r.mean()), 2),
            "win_rate": round(float((r > 0).mean() * 100), 1),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "avg_bars_held": round(float(g["bars"].mean()), 1),
            "exit_kinds": g["kind"].value_counts().to_dict(),
        }
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--step", type=int, default=5)
    args = parser.parse_args()

    filt, exits = run(args.years, args.step)
    result = {
        "generated_at": str(date.today()),
        "years": args.years,
        "step_days": args.step,
        "known_bias": "curated watchlist universe; relative comparisons valid",
        "part_a_filters": summarize_filters(filt),
        "part_b_exits": summarize_exits(exits),
    }
    out_path = _REPO_ROOT / "data" / f"backtest_p1_filters_exits_{args.years}y.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[P1] Written {out_path}")
    print(json.dumps({k: v for k, v in result["part_a_filters"].items() if k != "by_year"},
                     ensure_ascii=False, indent=2))
    print(json.dumps(result["part_b_exits"], ensure_ascii=False, indent=2))
