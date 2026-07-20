"""Append-only daily history series for the dashboard (e.g. divergence trend)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

_DOCS_DIR = Path(__file__).parent.parent.parent / "docs"


def update_divergence_history(
    divergence: dict[str, Any],
    today: date | None = None,
    max_days: int = 90,
) -> Path:
    """Append today's divergence snapshot to docs/divergence_history.json.

    Idempotent per day (re-running replaces today's entry). Keeps the most
    recent `max_days` points so the dashboard can draw a trend without unbounded
    growth. Skips writing a row when there is nothing to compare (n_compared=0)
    so empty early runs don't pollute the series.
    """
    today = today or date.today()
    out = _DOCS_DIR / "divergence_history.json"

    history: list[dict] = []
    if out.exists():
        try:
            history = json.loads(out.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []

    if (divergence or {}).get("n_compared"):
        entry = {
            "date": str(today),
            "avg_gap": divergence.get("avg_gap"),
            "missed_count": divergence.get("missed_count", 0),
            "overrated_count": divergence.get("overrated_count", 0),
            "n_compared": divergence.get("n_compared", 0),
        }
        history = [h for h in history if h.get("date") != str(today)]
        history.append(entry)

    history.sort(key=lambda h: h.get("date", ""))
    history = history[-max_days:]

    _DOCS_DIR.mkdir(parents=True, exist_ok=True)
    from src.atomic_io import atomic_write_text
    atomic_write_text(out, json.dumps(history, ensure_ascii=False, indent=2))
    print(f"[History] divergence_history → {len(history)} points")
    return out
