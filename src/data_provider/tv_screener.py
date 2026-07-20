"""TradingView screener funnel — broad-market momentum pre-filter.

Closes the universe gap found in the 2026-07 research: the weekly candidate
scan only covered current S&P 500 members, so mid-cap momentum leaders
(+200-400% names) were structurally invisible. This module pulls a liquid
momentum shortlist from the WHOLE US market via the free TradingView scanner
API (tradingview-screener package, no account needed), which then flows into
the same v2 scoring as everything else — the screener only nominates, our
own validated score decides.

Graceful degradation: any import/network failure returns [] and the scan
falls back to the S&P 500 universe.
"""
from __future__ import annotations


def fetch_momentum_universe(
    min_perf_6m: float = 30.0,
    min_price: float = 10.0,
    min_avg_vol_10d: int = 300_000,
    min_market_cap: float = 500_000_000,
    limit: int = 300,
) -> tuple[list[str], dict[str, str]]:
    """(symbols, {symbol: company_name}) of liquid US stocks with strong 6M
    momentum, best first. Company names ride along in the same free query so
    the candidate list can display them without extra API calls."""
    try:
        from tradingview_screener import Query, col
        q = (Query()
             .set_markets("america")
             .select("name", "description", "close", "average_volume_10d_calc",
                     "Perf.6M", "market_cap_basic")
             .where(
                 col("type") == "stock",
                 col("close") >= min_price,
                 col("average_volume_10d_calc") >= min_avg_vol_10d,
                 col("Perf.6M") >= min_perf_6m,
                 col("market_cap_basic") >= min_market_cap,
             )
             .order_by("Perf.6M", ascending=False)
             .limit(limit))
        _, df = q.get_scanner_data()
        # 'name' is the bare symbol; normalize share-class dots for yfinance
        syms, names = [], {}
        for _, row in df.iterrows():
            s = str(row["name"]).replace(".", "-").strip()
            if s and s not in names:
                syms.append(s)
                names[s] = str(row.get("description") or "").strip()
        return syms, names
    except Exception as exc:
        print(f"[TVScreener] unavailable, falling back to S&P500-only universe: {exc}")
        return [], {}
