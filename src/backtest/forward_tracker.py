"""Forward-return tracker: fills 3/5/10/20d returns for open watch_signals."""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from src.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

_HORIZONS = [3, 5, 10, 20]


def _trading_days_later(d: date, n: int) -> date:
    """Advance n *trading* (weekday) days from d. Was previously n *calendar*
    days, which understates elapsed trading time whenever the window crosses
    a weekend — e.g. a Friday signal's "3-day" target landed on Monday (only
    1 trading day elapsed), silently mixing horizons. Uses numpy's Mon-Fri
    business-day calendar (ignores market holidays — an approximation, but a
    large improvement over pure calendar days)."""
    return np.busday_offset(d.isoformat(), n, roll="forward").astype("datetime64[D]").astype(date)


def _fetch_period_low(symbol: str, start_date: date, end_date: date) -> float | None:
    """Lowest intraday Low between start_date (exclusive) and end_date
    (inclusive) — used to detect whether a stop would actually have been hit
    during the holding period, not just whether the close on the settlement
    date happened to be below it."""
    try:
        df = yf.download(symbol, start=str(start_date), end=str(end_date + timedelta(days=1)),
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        low = df["Low"].dropna()
        low.index = [i.date() if hasattr(i, "date") else i for i in low.index]
        window = low[(low.index > start_date) & (low.index <= end_date)]
        if window.empty:
            return None
        return float(window.min())
    except Exception as e:
        logger.warning("period-low fetch failed %s @ %s-%s: %s", symbol, start_date, end_date, e)
        return None


def _fetch_price(symbol: str, target_date: date) -> float | None:
    """Close on/near target_date. Handles yfinance's MultiIndex columns
    (('Close','AMD')) — the old code did float(df.loc[d,'Close']) on a Series and
    silently returned None for EVERY fill, so no forward returns ever populated."""
    start = target_date - timedelta(days=5)
    end = target_date + timedelta(days=5)
    try:
        df = yf.download(symbol, start=str(start), end=str(end),
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        # Flatten MultiIndex columns from newer yfinance
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = df["Close"].dropna()
        if close.empty:
            return None
        close.index = [i.date() if hasattr(i, "date") else i for i in close.index]
        if target_date in close.index:
            return float(close.loc[target_date])
        # nearest prior trading day (weekend/holiday target)
        prior = [d for d in close.index if d <= target_date]
        if prior:
            return float(close.loc[max(prior)])
        return float(close.iloc[-1])
    except Exception as e:
        logger.warning("price fetch failed %s @ %s: %s", symbol, target_date, e)
        return None


def _fetch_closes(symbol: str, start_date: date, end_date: date) -> pd.Series | None:
    """Daily closes start..end (inclusive), date-indexed. Used for the MA20
    trail-exit simulation, which needs ~20 bars BEFORE the signal date."""
    try:
        df = yf.download(symbol, start=str(start_date), end=str(end_date + timedelta(days=1)),
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = df["Close"].dropna()
        close.index = [i.date() if hasattr(i, "date") else i for i in close.index]
        return close
    except Exception as e:
        logger.warning("close-series fetch failed %s: %s", symbol, e)
        return None


_MA20_CAP_BARS = 40  # simulate at most 40 trading days, then exit at close


def _ma20_trail_exit(closes: pd.Series, signal_date: date, entry: float) -> dict | None:
    """Simulate the MA20 trailing exit for one signal: exit at the first
    post-signal close below the 20d SMA; cap at 40 bars. Returns the update
    dict when decided, None while the position is still 'open'."""
    if closes is None or entry in (None, 0):
        return None
    sma20 = closes.rolling(20).mean()
    post = closes[[d for d in closes.index if d > signal_date]]
    for bar_n, (d, c) in enumerate(post.items(), start=1):
        s = sma20.get(d)
        if s is not None and not pd.isna(s) and float(c) < float(s):
            return {"ma20_exit_return": round((float(c) / entry - 1) * 100, 2),
                    "ma20_exit_kind": "ma_trail"}
        if bar_n >= _MA20_CAP_BARS:
            return {"ma20_exit_return": round((float(c) / entry - 1) * 100, 2),
                    "ma20_exit_kind": "cap40"}
    return None


def _first_set(a, b):
    """None-aware coalescing: `a or b` treats an exact 0.0 return as missing
    (Codex audit #5), leaving such signals without an outcome forever."""
    return a if a is not None else b


def _classify_failure(ret3: float | None, ret10: float, stop_hit: int | None) -> str:
    """US port of tw-stock-ai's three-way failure attribution
    (kp_tw_failure_pattern_summary). Applied only to outcome='loss' signals:
    - stop_hit        停損觸發: the holding-period low touched the stop
    - momentum_fade   動能失靈: positive 3d start that reversed to a 10d loss
                      (US analog of tw's 題材失靈 — the move didn't carry)
    - weak_after_entry 進場後轉弱: never got going (3d already flat/negative)
    Priority mirrors tw's data: stop-hit dominates (their stop-hit bucket had
    100% stop_hit_rate), then the 3d sign splits the rest."""
    if stop_hit == 1:
        return "stop_hit"
    if ret3 is not None and ret3 > 0:
        return "momentum_fade"
    return "weak_after_entry"


def fill_open_signals(store: SQLiteStore) -> int:
    """Update returns for open signals where enough calendar days have passed."""
    today = date.today()
    open_signals = store.get_open_signals()
    updated = 0

    with store._connect() as conn:
        for sig in open_signals:
            symbol = sig["symbol"]
            signal_date = date.fromisoformat(sig["signal_date"])
            entry_price = sig.get("entry_price")
            stop_price = sig.get("stop_price")
            if not entry_price:
                continue

            updates: dict[str, object] = {}
            max_target = signal_date
            for h in _HORIZONS:
                col = f"return_{h}d"
                target = _trading_days_later(signal_date, h)
                max_target = max(max_target, target)
                if sig.get(col) is not None:
                    continue
                if target > today:
                    continue
                price = _fetch_price(symbol, target)
                if price:
                    ret = round((price - entry_price) / entry_price * 100, 2)
                    updates[col] = ret

            # stop_hit: did the LOW during the holding period ever touch the
            # stop, not just the close on a settlement date. Window aligned to
            # the 10d OUTCOME horizon (Codex audit-2 #7): the old max-horizon
            # (20d) window let a day-15 stop touch contaminate 10d attribution.
            if sig.get("stop_hit") is None and stop_price:
                stop_window_end = _trading_days_later(signal_date, 10)
                low = _fetch_period_low(symbol, signal_date, min(stop_window_end, today))
                if low is not None:
                    updates["stop_hit"] = 1 if low <= stop_price else 0

            if not updates:
                continue

            # Determine outcome when 10d return is available
            ret10 = _first_set(updates.get("return_10d"), sig.get("return_10d"))
            if ret10 is not None:
                if ret10 >= 10:
                    updates["outcome"] = "win"
                elif ret10 <= -5:
                    updates["outcome"] = "loss"
                    if sig.get("failure_reason") is None:
                        ret3 = _first_set(updates.get("return_3d"), sig.get("return_3d"))
                        sh = updates.get("stop_hit", sig.get("stop_hit"))
                        updates["failure_reason"] = _classify_failure(ret3, ret10, sh)
                else:
                    updates["outcome"] = "neutral"

            set_clause = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [sig["signal_date"], symbol]
            conn.execute(
                f"UPDATE watch_signals SET {set_clause} WHERE signal_date=? AND symbol=?",
                vals,
            )
            updated += 1

    print(f"[ForwardTracker] Updated {updated} signals")
    return updated


def fill_ma20_exits(store: SQLiteStore) -> int:
    """Decide the MA20 trail-exit simulation for every shadow signal that
    hasn't been decided yet. Runs SEPARATELY from the outcome loop because a
    signal's 10d outcome closes long before the 40-bar trail window does.
    One close-series fetch per symbol per run (cached across its signals)."""
    today = date.today()
    with store._connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT signal_date, symbol, grp, entry_price FROM shadow_signals "
            "WHERE ma20_exit_return IS NULL AND entry_price IS NOT NULL"
        ).fetchall()]
    if not rows:
        return 0

    earliest: dict[str, date] = {}
    for sig in rows:
        sd = date.fromisoformat(sig["signal_date"])
        if sig["symbol"] not in earliest or sd < earliest[sig["symbol"]]:
            earliest[sig["symbol"]] = sd

    closes_cache: dict[str, pd.Series | None] = {}
    updated = 0
    with store._connect() as conn:
        for sig in rows:
            sym = sig["symbol"]
            sd = date.fromisoformat(sig["signal_date"])
            if sd >= today:
                continue
            if sym not in closes_cache:
                closes_cache[sym] = _fetch_closes(
                    sym, earliest[sym] - timedelta(days=45), today)
            res = _ma20_trail_exit(closes_cache[sym], sd, sig.get("entry_price"))
            if not res:
                continue
            conn.execute(
                "UPDATE shadow_signals SET ma20_exit_return=?, ma20_exit_kind=? "
                "WHERE signal_date=? AND symbol=? AND grp=?",
                (res["ma20_exit_return"], res["ma20_exit_kind"],
                 sig["signal_date"], sym, sig["grp"]),
            )
            updated += 1
    print(f"[ForwardTracker] MA20 trail exits decided: {updated}")
    return updated


_ALPHA_HORIZONS = [5, 10]  # only these need SPY comparison (matches dashboard use)


def fill_shadow_signals(store: SQLiteStore) -> int:
    """Mirror of fill_open_signals for the shadow validation table (both groups:
    'shadow' and 'live_top'). Also computes alpha_5d/alpha_10d = stock forward
    return minus SPY forward return over the same window, so the comparison
    isolates stock-picking skill from market beta (a strong bull week inflates
    both groups' raw win-rate/return without telling us which picks better)."""
    today = date.today()
    open_sigs = store.get_open_shadow_signals()
    updated = 0
    spy_cache: dict[date, float | None] = {}

    def spy_at(d: date) -> float | None:
        if d not in spy_cache:
            spy_cache[d] = _fetch_price("SPY", d)
        return spy_cache[d]

    with store._connect() as conn:
        for sig in open_sigs:
            symbol = sig["symbol"]
            signal_date = date.fromisoformat(sig["signal_date"])
            entry_price = sig.get("entry_price")
            stop_price = sig.get("stop_price")
            if not entry_price:
                continue

            spy_entry = sig.get("spy_entry_price")
            if spy_entry is None:
                spy_entry = spy_at(signal_date)

            updates: dict[str, object] = {}
            if sig.get("spy_entry_price") is None and spy_entry is not None:
                updates["spy_entry_price"] = spy_entry

            max_target = signal_date
            for h in _HORIZONS:
                col = f"return_{h}d"
                stock_ret = sig.get(col)  # may already be filled from a prior run
                target = _trading_days_later(signal_date, h)
                max_target = max(max_target, target)
                need_return = stock_ret is None and target <= today
                need_alpha = (
                    h in _ALPHA_HORIZONS and spy_entry
                    and sig.get(f"alpha_{h}d") is None
                    and (stock_ret is not None or target <= today)
                )
                if not need_return and not need_alpha:
                    continue

                if need_return:
                    price = _fetch_price(symbol, target)
                    if price:
                        stock_ret = round((price - entry_price) / entry_price * 100, 2)
                        updates[col] = stock_ret

                # Decoupled from need_return: computes alpha for signals whose
                # return_Xd was already filled by an earlier run (the bug this
                # replaces would `continue` past alpha whenever return_Xd existed).
                if need_alpha and stock_ret is not None and target <= today:
                    spy_price = spy_at(target)
                    if spy_price:
                        spy_ret = round((spy_price - spy_entry) / spy_entry * 100, 2)
                        updates[f"spy_return_{h}d"] = spy_ret
                        updates[f"alpha_{h}d"] = round(stock_ret - spy_ret, 2)

            if sig.get("stop_hit") is None and stop_price:
                stop_window_end = _trading_days_later(signal_date, 10)  # align to 10d outcome
                low = _fetch_period_low(symbol, signal_date, min(stop_window_end, today))
                if low is not None:
                    updates["stop_hit"] = 1 if low <= stop_price else 0

            if not updates:
                continue
            ret10 = _first_set(updates.get("return_10d"), sig.get("return_10d"))
            if ret10 is not None:
                updates["outcome"] = "win" if ret10 >= 10 else "loss" if ret10 <= -5 else "neutral"
                if updates["outcome"] == "loss" and sig.get("failure_reason") is None:
                    ret3 = _first_set(updates.get("return_3d"), sig.get("return_3d"))
                    sh = updates.get("stop_hit", sig.get("stop_hit"))
                    updates["failure_reason"] = _classify_failure(ret3, ret10, sh)
            set_clause = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [sig["signal_date"], symbol, sig["grp"]]
            conn.execute(
                f"UPDATE shadow_signals SET {set_clause} WHERE signal_date=? AND symbol=? AND grp=?",
                vals,
            )
            updated += 1
    print(f"[ForwardTracker] Updated {updated} shadow signals")
    return updated
