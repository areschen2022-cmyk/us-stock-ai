"""Month-end live-vs-backtest adjudication report.

Feeds the three standing decisions:
  1. v2 -> official score?
  2. MA20 trail replace the 2xATR stop?
  3. AI verdict -> scoring weight?

Everything here reads the live forward-tracking tables only. Backtest numbers
are quoted from the stored reports for contrast, never recomputed here — the
whole point is to let live data adjudicate the backtest, so the two must stay
separately sourced.

Usage: python scripts/monthend_review.py [--as-of YYYY-MM-DD] [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest.forward_tracker import _trading_days_later
sys.stdout.reconfigure(encoding="utf-8")

_DB = Path(__file__).parent.parent / "data" / "us_stock_ai.sqlite3"
_MIN_N = 5  # below this a group is reported but never used for a decision


def _rows() -> list[dict]:
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM shadow_signals")]
    finally:
        conn.close()


def _f(row: dict, col: str) -> float | None:
    v = row.get(col)
    return None if v is None else float(v)


def _agg(vals: list[float]) -> dict:
    """Mean plus a paired-t-style significance hint. With n this small the
    t-stat is directional evidence, not proof — the report prints n beside it
    everywhere so a 4-sample 'win' can never be read as settled."""
    if not vals:
        return {"n": 0, "avg": None, "t": None}
    n = len(vals)
    avg = mean(vals)
    out = {"n": n, "avg": round(avg, 2), "t": None, "win_rate": None}
    out["win_rate"] = round(sum(1 for v in vals if v > 0) / n * 100, 1)
    if n >= 3:
        sd = stdev(vals)
        if sd > 0:
            out["t"] = round(avg / (sd / (n ** 0.5)), 2)
    return out


def group_table(rows: list[dict]) -> list[dict]:
    """Per-group forward performance. alpha_10d is the headline: it strips SPY
    beta, so a group is only 'good' if it beats the market, not just rises."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get("grp") or "?"), []).append(r)

    table = []
    for grp, rs in groups.items():
        rec = {"group": grp, "signals": len(rs)}
        for col in ("return_5d", "return_10d", "return_20d", "alpha_5d", "alpha_10d"):
            rec[col] = _agg([v for r in rs if (v := _f(r, col)) is not None])
        stops = [r for r in rs if r.get("stop_hit") is not None]
        rec["stop_hit_rate"] = (
            round(sum(1 for r in stops if int(r["stop_hit"]) == 1) / len(stops) * 100, 1)
            if stops else None
        )
        table.append(rec)
    return sorted(table, key=lambda d: -d["signals"])


def exit_comparison(rows: list[dict]) -> list[dict]:
    """hold-20d vs 2xATR-stop vs MA20-trail, per group AND pooled.

    The 2xATR column reconstructs what the stop would have returned: if the low
    ever touched the stop we book the stop loss, otherwise the trade rode to
    20d. That is the same reconstruction performance.py uses, kept identical so
    the month-end number and the dashboard number cannot drift apart.
    """
    def one(rs: list[dict], label: str) -> dict | None:
        done = [r for r in rs
                if _f(r, "return_20d") is not None and _f(r, "ma20_exit_return") is not None]
        if not done:
            return None
        hold = [_f(r, "return_20d") for r in done]
        trail = [_f(r, "ma20_exit_return") for r in done]
        stop = []
        for r in done:
            sp, ep = _f(r, "stop_price"), _f(r, "entry_price")
            if int(r.get("stop_hit_20d") or 0) == 1 and sp and ep:
                stop.append((sp / ep - 1) * 100)
            else:
                stop.append(_f(r, "return_20d"))
        # paired differences — same signals under both rules, so the pairing
        # removes signal-selection noise and isolates the exit rule itself
        diff = [t - h for t, h in zip(trail, hold)]
        return {
            "group": label, "n": len(done),
            "hold20": round(mean(hold), 2),
            "stop2atr": round(mean(stop), 2),
            "ma20_trail": round(mean(trail), 2),
            "ma20_minus_hold": _agg(diff),
            "ma20_beats_hold_pct": round(sum(1 for d in diff if d > 0) / len(diff) * 100, 1),
        }

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get("grp") or "?"), []).append(r)
    out = [c for g, rs in groups.items() if (c := one(rs, g))]
    out.sort(key=lambda d: -d["n"])
    pooled = one(rows, "POOLED")
    if pooled:
        out.append(pooled)
    return out


def ai_discrimination(rows: list[dict]) -> dict:
    """Does the DeepSeek verdict separate outcomes? Buy should beat Avoid on
    alpha; if it does not, weighting the AI into the score adds noise."""
    out = {}
    for grp in ("ai_buy", "ai_hold", "ai_avoid"):
        rs = [r for r in rows if r.get("grp") == grp]
        out[grp] = {
            "signals": len(rs),
            "alpha_10d": _agg([v for r in rs if (v := _f(r, "alpha_10d")) is not None]),
            "return_10d": _agg([v for r in rs if (v := _f(r, "return_10d")) is not None]),
        }
    b, a = out["ai_buy"]["alpha_10d"], out["ai_avoid"]["alpha_10d"]
    out["buy_minus_avoid_alpha10"] = (
        round(b["avg"] - a["avg"], 2) if b["avg"] is not None and a["avg"] is not None else None
    )
    out["min_n"] = min(out[g]["alpha_10d"]["n"] for g in ("ai_buy", "ai_hold", "ai_avoid"))
    return out


def v2_check(rows: list[dict]) -> dict:
    """score_v2_sa = signals selected by v2's S/A tier. Compare against live_top
    (what the official v3 score actually surfaced) on alpha — that is the
    like-for-like test of 'should v2 become the official score'."""
    out = {}
    for grp in ("score_v2_sa", "live_top"):
        rs = [r for r in rows if r.get("grp") == grp]
        out[grp] = {
            "signals": len(rs),
            "alpha_10d": _agg([v for r in rs if (v := _f(r, "alpha_10d")) is not None]),
            "alpha_5d": _agg([v for r in rs if (v := _f(r, "alpha_5d")) is not None]),
        }
    v2a, lta = out["score_v2_sa"]["alpha_10d"], out["live_top"]["alpha_10d"]
    out["v2_minus_live_alpha10"] = (
        round(v2a["avg"] - lta["avg"], 2)
        if v2a["avg"] is not None and lta["avg"] is not None else None
    )
    return out


_BENCH = ("SPY", "MTUM", "IWM")


def factor_relative(rows: list[dict]) -> dict:
    """Alpha against a momentum benchmark, not just SPY.

    Our selection is explicitly momentum (RS rating, Minervini template,
    52w-high proximity, volume accumulation). Judging it purely against SPY
    conflates two different questions: 'do we pick well?' and 'is the momentum
    factor in favour?'. In 2026-06/07 MTUM lost 8.7pp to SPY, which made every
    group's SPY-alpha look catastrophic while several were in fact beating
    their own factor. Report both; SPY-alpha alone is a mis-specified yardstick.
    """
    import pandas as pd
    import yfinance as yf

    dates = [r["signal_date"] for r in rows if r.get("return_10d") is not None]
    if not dates:
        return {}
    px = yf.download(list(_BENCH), start=min(dates), end=None,
                     auto_adjust=True, progress=False)["Close"]
    px.index = px.index.tz_localize(None).normalize()

    def at(sym: str, d: date) -> float | None:
        s = px[sym].dropna()
        s = s[s.index <= pd.Timestamp(d)]
        return float(s.iloc[-1]) if len(s) else None

    groups: dict[str, list[dict]] = {}
    for r in rows:
        stock = _f(r, "return_10d")
        if stock is None:
            continue
        sd = date.fromisoformat(r["signal_date"])
        tgt = _trading_days_later(sd, 10)
        rec = {"stock": stock}
        for b in _BENCH:
            entry, exit_ = at(b, sd), at(b, tgt)
            if entry and exit_:
                rec[b] = (exit_ / entry - 1) * 100
        if len(rec) == len(_BENCH) + 1:  # every benchmark priced, else drop the row
            groups.setdefault(str(r.get("grp") or "?"), []).append(rec)

    def block(rs: list[dict]) -> dict:
        out = {"n": len(rs), "raw_return_10d": _agg([x["stock"] for x in rs])["avg"]}
        for b in _BENCH:
            out[f"alpha_vs_{b}"] = _agg([x["stock"] - x[b] for x in rs])
        return out

    result = {g: block(rs) for g, rs in groups.items() if len(rs) >= _MIN_N}
    allr = [x for rs in groups.values() for x in rs]
    if allr:
        result["POOLED"] = block(allr)
    return result


def entry_quality(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get("entry_quality") or "未標記"), []).append(r)
    out = []
    for label, rs in groups.items():
        out.append({
            "entry_quality": label,
            "signals": len(rs),
            "alpha_10d": _agg([v for r in rs if (v := _f(r, "alpha_10d")) is not None]),
        })
    return sorted(out, key=lambda d: -d["signals"])


def failure_attribution(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[float]] = {}
    for r in rows:
        reason = r.get("failure_reason")
        v = _f(r, "return_10d")
        if reason and v is not None:
            groups.setdefault(str(reason), []).append(v)
    return sorted(
        ({"reason": k, **_agg(v)} for k, v in groups.items()),
        key=lambda d: -d["n"],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()

    rows = _rows()
    payload = {
        "as_of": as_of.isoformat(),
        "total_signals": len(rows),
        "date_range": [min(r["signal_date"] for r in rows), max(r["signal_date"] for r in rows)],
        "min_n_for_decision": _MIN_N,
        "groups": group_table(rows),
        "exit_comparison": exit_comparison(rows),
        "ai_discrimination": ai_discrimination(rows),
        "v2_check": v2_check(rows),
        "factor_relative": factor_relative(rows),
        "entry_quality": entry_quality(rows),
        "failure_attribution": failure_attribution(rows),
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[MonthEnd] written {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
