"""Build performance_data.json for Trading Knowledge Hub ingestion."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

from src.notifier.telegram import ACTION_ZH, THEME_ZH
from src.storage.sqlite_store import SQLiteStore

_DOCS_DIR = Path(__file__).parent.parent.parent / "docs"


def _pct(value: float) -> float:
    return round(value, 1)


def _load_themes(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in data if item]


def _row_stats(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    completed = [row for row in rows if row.get("return_5d") is not None]
    wins = [row for row in completed if float(row.get("return_5d") or 0) > 0]
    stop_hits = [row for row in completed if int(row.get("stop_hit") or 0) == 1]
    returns = [float(row.get("return_5d") or 0) for row in completed]

    # failure attribution buckets (ported from tw-stock-ai's 失敗歸因) —
    # losses only, keyed by forward_tracker._classify_failure's taxonomy
    failures: dict[str, list[float]] = {}
    for row in rows:
        reason = row.get("failure_reason")
        if reason and row.get("return_10d") is not None:
            failures.setdefault(str(reason), []).append(float(row["return_10d"]))

    return {
        "signals": len(rows),
        "completed": len(completed),
        "win_rate_5d": _pct(len(wins) / len(completed) * 100) if completed else 0.0,
        "avg_return_5d": _pct(mean(returns)) if returns else 0.0,
        "stop_hit_rate": _pct(len(stop_hits) / len(completed) * 100) if completed else 0.0,
        "failure_attribution": {
            reason: {"n": len(rets), "avg_return_10d": _pct(mean(rets))}
            for reason, rets in sorted(failures.items(), key=lambda kv: -len(kv[1]))
        },
    }


def _group_stats(groups: dict[str, list[dict[str, Any]]], labels: dict[str, str]) -> list[dict[str, Any]]:
    result = []
    for key, rows in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        result.append({"label": labels.get(key, key), **_row_stats(rows)})
    return result


def build_performance_payload(store: SQLiteStore, as_of: date | None = None) -> dict[str, Any]:
    """watch_signals only gets a row when a stock hits S/A grade (>=65),
    which — per the scoring-ceiling audit — has never actually happened in
    this market; the table stays permanently empty and this payload used to
    silently report all-zero stats forever (looked like "no signals yet"
    rather than "the feeding table is structurally unreachable").

    Primary source is now shadow_signals grp='live_top' (today's actual
    top-10 picks, populated every run) with 'shadow' (RS/Minervini picks) as
    a secondary comparison group. watch_signals is kept as a third group in
    case S/A grades start appearing after a scoring recalibration."""
    as_of = as_of or date.today()
    with store._connect() as conn:
        conn.row_factory = __import__("sqlite3").Row
        watch_rows = [dict(row) for row in conn.execute("SELECT * FROM watch_signals").fetchall()]
        live_top_rows = [dict(row) for row in conn.execute(
            "SELECT * FROM shadow_signals WHERE grp='live_top'").fetchall()]
        shadow_rows = [dict(row) for row in conn.execute(
            "SELECT * FROM shadow_signals WHERE grp='shadow'").fetchall()]

    theme_groups: dict[str, list[dict[str, Any]]] = {}
    action_groups: dict[str, list[dict[str, Any]]] = {}
    for row in watch_rows:
        action = str(row.get("action") or "未分類")
        action_groups.setdefault(action, []).append(row)
        themes = _load_themes(row.get("themes_json")) or ["未分類"]
        for theme in themes:
            theme_groups.setdefault(theme, []).append(row)

    # shadow_signals rows use live_grade instead of watch_signals' action —
    # group by grade tier so there's still a meaningful breakdown even when
    # watch_signals (and therefore action_stats) is empty.
    grade_groups: dict[str, list[dict[str, Any]]] = {}
    for row in live_top_rows:
        grade = str(row.get("live_grade") or "未分級")
        grade_groups.setdefault(grade, []).append(row)

    # exit comparison (live adjudication of the 10y exit sweep): for signals
    # where both the 20d hold return and the MA20-trail simulation are decided,
    # compare hold-20d vs 2ATR-stop-clipped vs MA20-trail per group
    def _exit_comparison(rows: list[dict[str, Any]]) -> dict | None:
        done = [r for r in rows
                if r.get("return_20d") is not None and r.get("ma20_exit_return") is not None]
        if len(done) < 5:
            return None
        hold = [float(r["return_20d"]) for r in done]
        trail = [float(r["ma20_exit_return"]) for r in done]
        stop_clipped = []
        for r in done:
            if int(r.get("stop_hit") or 0) == 1 and r.get("stop_price") and r.get("entry_price"):
                stop_clipped.append((float(r["stop_price"]) / float(r["entry_price"]) - 1) * 100)
            else:
                stop_clipped.append(float(r["return_20d"]))
        return {"n": len(done),
                "hold20_avg": _pct(mean(hold)),
                "stop2atr_avg": _pct(mean(stop_clipped)),
                "ma20_trail_avg": _pct(mean(trail))}

    exit_comparison = {
        grp: cmp for grp, rows_g in (("live_top", live_top_rows), ("shadow", shadow_rows))
        if (cmp := _exit_comparison(rows_g)) is not None
    }

    # entry-quality validation (port of tw's 進場條件保護 measurement): group
    # forward returns by the entry_quality label stamped at signal time, so we
    # can verify on US data whether 可進場 really beats 等拉回/避免追高
    eq_groups: dict[str, list[dict[str, Any]]] = {}
    for row in live_top_rows + shadow_rows:
        label = str(row.get("entry_quality") or "未標記")
        eq_groups.setdefault(label, []).append(row)

    primary_rows = live_top_rows or watch_rows
    return {
        "as_of": as_of.isoformat(),
        "primary_source": "live_top" if live_top_rows else "watch_signals",
        "stats": _row_stats(primary_rows),
        "live_top_stats": _row_stats(live_top_rows) if live_top_rows else None,
        "shadow_stats": _row_stats(shadow_rows) if shadow_rows else None,
        "watch_signals_stats": _row_stats(watch_rows) if watch_rows else None,
        "theme_stats": _group_stats(theme_groups, THEME_ZH),
        "action_stats": _group_stats(action_groups, ACTION_ZH),
        "grade_stats": _group_stats(grade_groups, {}),
        "entry_quality_stats": _group_stats(eq_groups, {}),
        "exit_comparison": exit_comparison,
    }


def write_performance_json(payload: dict[str, Any], output_dir: Path | None = None) -> Path:
    output_dir = output_dir or _DOCS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "performance_data.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Performance] Written {out}")
    return out
