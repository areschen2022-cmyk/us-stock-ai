"""Forward-return tracker: fills 3/5/10/20d returns for open watch_signals."""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from src.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)

_HORIZONS = [3, 5, 10, 20]


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
            if not entry_price:
                continue

            updates: dict[str, object] = {}
            for h in _HORIZONS:
                col = f"return_{h}d"
                if sig.get(col) is not None:
                    continue
                target = signal_date + timedelta(days=h)
                if target > today:
                    continue
                price = _fetch_price(symbol, target)
                if price:
                    ret = round((price - entry_price) / entry_price * 100, 2)
                    updates[col] = ret

            if not updates:
                continue

            # Determine outcome when 10d return is available
            ret10 = updates.get("return_10d") or sig.get("return_10d")
            if ret10 is not None:
                if ret10 >= 10:
                    updates["outcome"] = "win"
                elif ret10 <= -5:
                    updates["outcome"] = "loss"
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


def fill_shadow_signals(store: SQLiteStore) -> int:
    """Mirror of fill_open_signals for the shadow validation table (both groups:
    'shadow' and 'live_top'). Lets us compare forward returns apples-to-apples."""
    today = date.today()
    open_sigs = store.get_open_shadow_signals()
    updated = 0
    with store._connect() as conn:
        for sig in open_sigs:
            symbol = sig["symbol"]
            signal_date = date.fromisoformat(sig["signal_date"])
            entry_price = sig.get("entry_price")
            if not entry_price:
                continue
            updates: dict[str, object] = {}
            for h in _HORIZONS:
                col = f"return_{h}d"
                if sig.get(col) is not None:
                    continue
                target = signal_date + timedelta(days=h)
                if target > today:
                    continue
                price = _fetch_price(symbol, target)
                if price:
                    updates[col] = round((price - entry_price) / entry_price * 100, 2)
            if not updates:
                continue
            ret10 = updates.get("return_10d") or sig.get("return_10d")
            if ret10 is not None:
                updates["outcome"] = "win" if ret10 >= 10 else "loss" if ret10 <= -5 else "neutral"
            set_clause = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [sig["signal_date"], symbol, sig["grp"]]
            conn.execute(
                f"UPDATE shadow_signals SET {set_clause} WHERE signal_date=? AND symbol=? AND grp=?",
                vals,
            )
            updated += 1
    print(f"[ForwardTracker] Updated {updated} shadow signals")
    return updated
