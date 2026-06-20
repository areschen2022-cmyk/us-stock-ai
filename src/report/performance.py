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

    return {
        "signals": len(rows),
        "completed": len(completed),
        "win_rate_5d": _pct(len(wins) / len(completed) * 100) if completed else 0.0,
        "avg_return_5d": _pct(mean(returns)) if returns else 0.0,
        "stop_hit_rate": _pct(len(stop_hits) / len(completed) * 100) if completed else 0.0,
    }


def _group_stats(groups: dict[str, list[dict[str, Any]]], labels: dict[str, str]) -> list[dict[str, Any]]:
    result = []
    for key, rows in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        result.append({"label": labels.get(key, key), **_row_stats(rows)})
    return result


def build_performance_payload(store: SQLiteStore, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    with store._connect() as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = [dict(row) for row in conn.execute("SELECT * FROM watch_signals").fetchall()]

    theme_groups: dict[str, list[dict[str, Any]]] = {}
    action_groups: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        action = str(row.get("action") or "未分類")
        action_groups.setdefault(action, []).append(row)
        themes = _load_themes(row.get("themes_json")) or ["未分類"]
        for theme in themes:
            theme_groups.setdefault(theme, []).append(row)

    return {
        "as_of": as_of.isoformat(),
        "stats": _row_stats(rows),
        "theme_stats": _group_stats(theme_groups, THEME_ZH),
        "action_stats": _group_stats(action_groups, ACTION_ZH),
    }


def write_performance_json(payload: dict[str, Any], output_dir: Path | None = None) -> Path:
    output_dir = output_dir or _DOCS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "performance_data.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Performance] Written {out}")
    return out
