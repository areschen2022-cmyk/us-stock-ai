"""Score v2 on POINT-IN-TIME S&P 500 membership — kills the index-foresight
bias that the current-constituents run (backtest_score_v2_sp500.py) declared.

Membership source: fja05680/sp500 (public dataset of historical S&P 500
component changes; each row = date + full member list). At every sampled day
the cross-section only contains stocks that were actually in the index THAT
day — no credit for companies that joined later.

REMAINING BIAS (declared, unavoidable without paid data): delisted/acquired
tickers have no yfinance history, so losers that left the market entirely are
still absent. PIT membership removes the *inclusion foresight* half of the
bias; the *delisting survivorship* half remains and inflates absolute levels.
The S-vs-rest spread stays the informative quantity.

Usage: python scripts/backtest_score_v2_pit.py [--years 10] [--step 5]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.strategy import us_market
from src.scoring.grade import grade_label
from scripts.backtest_score_v2_sp500 import fetch_all

_HORIZONS = [5, 20]
_WARMUP_DAYS = 280
_PIT_URL = ("https://raw.githubusercontent.com/fja05680/sp500/master/"
            "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv")


def load_pit_membership() -> pd.DataFrame:
    """Rows: date (Timestamp), tickers (set[str]). Sorted by date."""
    resp = requests.get(_PIT_URL, headers={"User-Agent": "Mozilla/5.0 (research script)"},
                        timeout=60)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df["tickers"] = df["tickers"].apply(
        lambda s: {t.strip().replace(".", "-") for t in str(s).split(",") if t.strip()})
    return df.sort_values("date").reset_index(drop=True)


def members_asof(pit: pd.DataFrame, when) -> set[str]:
    rows = pit[pit["date"] <= when]
    return rows.iloc[-1]["tickers"] if len(rows) else set()


def run(years: int, step: int) -> list[dict]:
    pit = load_pit_membership()
    print(f"[PIT] membership table: {len(pit)} change-dates "
          f"({pit['date'].min().date()} .. {pit['date'].max().date()})")

    # union of every ticker that was ever a member during the window
    cutoff = pd.Timestamp.today() - pd.DateOffset(years=years)
    union: set[str] = set()
    for _, row in pit[pit["date"] >= cutoff - pd.DateOffset(years=2)].iterrows():
        union |= row["tickers"]
    union = sorted(union)
    print(f"[PIT] union universe over window: {len(union)} tickers (incl. later-delisted)")

    data = fetch_all(union + ["SPY"], years)
    spy_df = data.pop("SPY", None)
    if spy_df is None or spy_df.empty:
        raise RuntimeError("SPY fetch failed")
    fetched = len(data)
    print(f"[PIT] {fetched}/{len(union)} tickers have usable history "
          f"(gap = delisting survivorship, declared)")
    spy_close = spy_df["Close"].astype(float)
    spy_index = spy_df.index
    n_days = len(spy_index)

    records: list[dict] = []
    for day_i in range(_WARMUP_DAYS, n_days - max(_HORIZONS), step):
        signal_date = spy_index[day_i]
        member_set = members_asof(pit, signal_date)
        if not member_set:
            continue

        windows: dict[str, pd.DataFrame] = {}
        composites: dict[str, float] = {}
        for sym in member_set:
            df = data.get(sym)
            if df is None:
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
            total, _ = us_market.score_v2(window, rs_pct.get(sym))
            grade = grade_label(total)
            if grade not in ("S", "A") and (hash(sym) + day_i) % 3:
                continue
            full = data[sym]
            entry_pos = full.index.get_loc(window.index[-1])
            if entry_pos + max(_HORIZONS) >= len(full):
                continue
            rec = {"date": str(signal_date.date())[:10], "symbol": sym, "grade": grade}
            for h in _HORIZONS:
                fwd = float(full["Close"].iloc[entry_pos + h]) / price - 1
                spy_fwd = float(spy_close.iloc[min(day_i + h, len(spy_close) - 1)]) / spy_entry - 1
                rec[f"ret_{h}d"] = round(fwd * 100, 2)
                rec[f"alpha_{h}d"] = round((fwd - spy_fwd) * 100, 2)
            records.append(rec)

        if (day_i - _WARMUP_DAYS) % (step * 40) == 0:
            print(f"[PIT] ...{str(signal_date.date())[:10]} members-with-data={len(composites)} records={len(records)}")
    return records


def summarize(records: list[dict]) -> dict:
    df = pd.DataFrame(records)
    out: dict = {"n_obs": len(df), "n_dates": df["date"].nunique()}

    def bucket(sub: pd.DataFrame) -> dict:
        a5, a20 = sub["alpha_5d"].dropna(), sub["alpha_20d"].dropna()
        return {"n": len(sub), "avg_alpha_5d": round(float(a5.mean()), 2),
                "avg_alpha_20d": round(float(a20.mean()), 2),
                "alpha20_win": round(float((a20 > 0).mean() * 100), 1)}

    out["by_grade"] = {g: bucket(grp) for g, grp in df.groupby("grade")}
    df["year"] = pd.to_datetime(df["date"]).dt.year
    out["S_by_year"] = {
        str(yr): {"n": int((g.grade == "S").sum()),
                  "S_alpha20": round(float(g[g.grade == "S"]["alpha_20d"].dropna().mean()), 2)
                  if (g.grade == "S").any() else None,
                  "rest_alpha20": round(float(g[~g.grade.isin(["S", "A"])]["alpha_20d"].dropna().mean()), 2)}
        for yr, g in df.groupby("year")
    }
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--step", type=int, default=5)
    args = parser.parse_args()

    records = run(args.years, args.step)
    summary = summarize(records)
    out_path = _REPO_ROOT / "data" / f"backtest_score_v2_pit_{args.years}y.json"
    out_path.write_text(json.dumps({
        "generated_at": str(date.today()),
        "years": args.years, "step_days": args.step,
        "membership_source": "fja05680/sp500 historical components (public dataset)",
        "known_bias": "delisting survivorship remains (no yfinance data for dead tickers); "
                      "index-foresight bias removed via point-in-time membership",
        "summary": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[PIT] Written {out_path}")
    print(json.dumps(summary["by_grade"], ensure_ascii=False, indent=2))
