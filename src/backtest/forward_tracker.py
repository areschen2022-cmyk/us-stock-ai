"""Forward-return tracker: fills 3/5/10/20d returns for open watch_signals."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf

from src.storage.sqlite_store import SQLiteStore

_HORIZONS = [3, 5, 10, 20]


def _fetch_price(symbol: str, target_date: date) -> float | None:
    start = target_date - timedelta(days=3)
    end = target_date + timedelta(days=3)
    try:
        df = yf.download(symbol, start=str(start), end=str(end), progress=False, auto_adjust=True)
        if df.empty:
            return None
        df.index = df.index.date
        if target_date in df.index:
            return float(df.loc[target_date, "Close"])
        return float(df["Close"].iloc[-1])
    except Exception:
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
