"""IBD-style market timing signals: distribution days + follow-through day.

Cloud-friendly port of the ibd-distribution-day-monitor / ftd-detector /
market-top-detector skill trio (tradermonty) so the daily GitHub Actions run
can compute them without local skills or FMP quota — everything derives from
yfinance daily OHLCV that the pipeline already depends on.

Rules implemented (O'Neil):
- Distribution day: index closes down >= 0.2% on volume higher than the prior
  session. Counted over the trailing 25 sessions; a DD is invalidated once the
  index closes 5% or more above that DD's close.
- Risk zones by live DD count (both indexes, take the worse):
  0-2 NORMAL / 3-4 CAUTION / 5-6 HIGH / 7+ SEVERE.
- Follow-through day: after a >=3% decline, rally day 1 = first up close off
  the swing low; a valid FTD is a >=1.5% up close on higher volume on rally
  day 4-15. Undercutting the swing low resets the attempt.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import yfinance as yf

_DD_WINDOW = 25
_DD_DOWN_PCT = -0.2
_DD_INVALIDATE_RALLY = 1.05
_FTD_MIN_DECLINE = 3.0
_FTD_GAIN_PCT = 1.5
_FTD_DAY_MIN, _FTD_DAY_MAX = 4, 15

_RISK_ZONES = [(7, "SEVERE", "嚴重"), (5, "HIGH", "高風險"), (3, "CAUTION", "警戒"), (0, "NORMAL", "正常")]


def _fmt_date(v) -> str:
    """Index values may be Timestamps (local yfinance) or plain strings (the
    CI pipeline's frames) — render either as YYYY-MM-DD."""
    d = getattr(v, "date", None)
    return str(d()) if callable(d) else str(v)[:10]


def _fetch_daily(symbol: str, period: str = "2y") -> pd.DataFrame | None:
    # 2y (not 6mo): the 200MA regime read needs >=200 bars; 6mo (~125) made
    # the standalone-fetch path degrade posture to 資料不足 (caught in audit)
    try:
        df = yf.download(symbol, period=period, auto_adjust=True, progress=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df.dropna(subset=["Close"])
    except Exception:
        return None


def distribution_days(df: pd.DataFrame) -> dict[str, Any]:
    """Count live distribution days in the trailing 25 sessions."""
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    if len(close) < _DD_WINDOW + 1:
        return {"count": None, "dates": []}
    chg_pct = close.pct_change() * 100
    latest = float(close.iloc[-1])
    dates: list[str] = []
    for i in range(len(close) - _DD_WINDOW, len(close)):
        if chg_pct.iloc[i] <= _DD_DOWN_PCT and volume.iloc[i] > volume.iloc[i - 1]:
            dd_close = float(close.iloc[i])
            # 5% rally above the DD close invalidates it
            if float(close.iloc[i:].max()) < dd_close * _DD_INVALIDATE_RALLY:
                dates.append(_fmt_date(df.index[i]))
    _ = latest
    return {"count": len(dates), "dates": dates}


def ftd_state(df: pd.DataFrame) -> dict[str, Any]:
    """O'Neil follow-through-day state machine on the last ~60 sessions."""
    close = df["Close"].astype(float).tail(70)
    volume = df["Volume"].astype(float).tail(70)
    idx = df.index[-len(close):]
    if len(close) < 20:
        return {"state": "NO_DATA", "label": "資料不足"}

    # Anchor on the MOST RECENT correction: the window's peak first, then the
    # low after it (a global argmin would latch onto an older, deeper low
    # whose recovery is already history)
    peak_pos = int(close.values.argmax())
    peak = float(close.iloc[peak_pos])
    if peak_pos >= len(close) - 2:
        return {"state": "UPTREND", "label": "指數在近期高點（不需 FTD）"}
    trough_pos = peak_pos + int(close.iloc[peak_pos:].values.argmin())
    trough = float(close.iloc[trough_pos])
    decline_pct = (peak - trough) / peak * 100

    latest = float(close.iloc[-1])
    if decline_pct < _FTD_MIN_DECLINE:
        return {"state": "UPTREND", "label": "無修正（不需 FTD）"}

    # rally attempt starts at the first up close after the swing low;
    # undercutting the low resets (using the post-low minimum as the real low)
    if latest < trough:
        return {"state": "NO_SIGNAL", "label": "仍創新低，無反彈嘗試"}
    rally_day1_pos = None
    for i in range(trough_pos + 1, len(close)):
        if close.iloc[i] > close.iloc[i - 1]:
            rally_day1_pos = i
            break
    if rally_day1_pos is None:
        return {"state": "NO_SIGNAL", "label": "低點後尚無上漲日"}

    day_count = len(close) - rally_day1_pos
    swing_low_date = _fmt_date(idx[trough_pos])

    chg_pct = close.pct_change() * 100
    for i in range(rally_day1_pos, len(close)):
        day_n = i - rally_day1_pos + 1
        if (_FTD_DAY_MIN <= day_n <= _FTD_DAY_MAX
                and chg_pct.iloc[i] >= _FTD_GAIN_PCT
                and volume.iloc[i] > volume.iloc[i - 1]):
            return {
                "state": "FTD_CONFIRMED",
                "label": f"FTD 已確認（{_fmt_date(idx[i])}，第{day_n}天，+{chg_pct.iloc[i]:.1f}%）",
                "ftd_date": _fmt_date(idx[i]),
                "swing_low_date": swing_low_date,
                "decline_pct": round(decline_pct, 1),
            }

    if day_count < _FTD_DAY_MIN:
        state, label = "RALLY_ATTEMPT", f"反彈嘗試第{day_count}天（FTD 窗口第4天起）"
    elif day_count <= _FTD_DAY_MAX:
        state, label = "FTD_WINDOW", f"FTD 窗口內第{day_count}天，尚未出現確認日"
    else:
        state, label = "WINDOW_PASSED", f"反彈第{day_count}天，FTD 窗口已過未確認（保守）"
    return {
        "state": state, "label": label,
        "swing_low_date": swing_low_date,
        "decline_pct": round(decline_pct, 1),
        "rally_day": day_count,
    }


def _posture(above_200ma: bool | None, ftd: dict | None) -> dict[str, str]:
    """主軸判讀 (validated by the 30y index backtest, kp_us_market_timing_30y):
    200MA regime (above: 66.9% 20d win vs below: 56.7%) + FTD state (57 events,
    35% failure rate, +4.9% avg 120d). Distribution days showed NO predictive
    power over 30y and are demoted to reference-only display."""
    state = (ftd or {}).get("state")
    if above_200ma is None:
        return {"label": "資料不足", "detail": ""}
    if above_200ma:
        if state in ("UPTREND", None):
            return {"label": "多頭（維持既有部位）",
                    "detail": "站上200MA且無進行中修正。"}
        if state == "FTD_CONFIRMED":
            return {"label": "底部確認（可試探性進場）",
                    "detail": "FTD已確認：首倉小額（如25%），跌破反彈起漲低點即停損；"
                              "30年歷史FTD失敗率35%，確認有效再分批加碼。"}
        return {"label": "多頭中修正（等FTD確認）",
                "detail": "站上200MA但修正未獲FTD確認：新多單保守、首倉宜小。"}
    if state == "FTD_CONFIRMED":
        return {"label": "防禦中出現FTD（僅試探倉）",
                "detail": "跌破200MA的FTD可靠度較低，僅小額試探並嚴設停損。"}
    return {"label": "防禦（跌破200MA）",
            "detail": "200MA之下勝率顯著下降（30年：56.7% vs 66.9%），不做新多。"}


def market_timing_summary(spy_df: pd.DataFrame | None = None,
                          qqq_df: pd.DataFrame | None = None) -> dict[str, Any]:
    """Full block for dashboard/telegram. Fetches SPY/QQQ if not provided.

    2026-07-14 restructure per the 30y validation: primary read = 200MA regime
    + FTD state ('posture'); distribution-day counts demoted to reference-only
    ('dd_zone', explicitly annotated as non-predictive in bull trends)."""
    spy_df = spy_df if spy_df is not None and not spy_df.empty else _fetch_daily("SPY")
    qqq_df = qqq_df if qqq_df is not None and not qqq_df.empty else _fetch_daily("QQQ")

    out: dict[str, Any] = {"distribution": {}, "ftd": None, "dd_zone": None,
                           "spy_above_200ma": None, "posture": None}
    counts = []
    for name, df in (("SPY", spy_df), ("QQQ", qqq_df)):
        if df is None:
            out["distribution"][name] = {"count": None, "dates": []}
            continue
        dd = distribution_days(df)
        out["distribution"][name] = dd
        if dd["count"] is not None:
            counts.append(dd["count"])

    if counts:
        worst = max(counts)
        for threshold, zone, zh in _RISK_ZONES:
            if worst >= threshold:
                out["dd_zone"] = {
                    "zone": zone, "label": zh, "worst_count": worst,
                    "note": "參考指標：30年驗證顯示分配日計數在多頭中無預測力"
                            "（SEVERE日後市反而偏強），勿作為降曝險依據",
                }
                break

    if spy_df is not None and len(spy_df) >= 200:
        close = spy_df["Close"].astype(float)
        out["spy_above_200ma"] = bool(float(close.iloc[-1]) > float(close.tail(200).mean()))
        out["ftd"] = ftd_state(spy_df)

    out["posture"] = _posture(out["spy_above_200ma"], out["ftd"])
    return out
