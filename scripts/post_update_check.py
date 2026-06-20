"""Post-run health check after daily update, prints optimization suggestions."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.storage.sqlite_store import SQLiteStore

_DB = _ROOT / "data" / "us_stock_ai.sqlite3"
_CACHE_DIR = _ROOT / "data" / "cache"
_DOCS = _ROOT / "docs" / "dashboard_data.json"


def _check_db() -> list[str]:
    issues: list[str] = []
    SQLiteStore(_DB)

    try:
        conn = sqlite3.connect(_DB)
        today = str(date.today())
        n = conn.execute(
            "SELECT COUNT(*) FROM daily_scores WHERE as_of_date=?",
            (today,),
        ).fetchone()[0]
        if n == 0:
            issues.append(f"WARNING: No scores for today ({today}) in daily_scores")
        else:
            print(f"  [DB] daily_scores for today: {n} rows OK")

        retry_q = conn.execute(
            "SELECT COUNT(*) FROM data_retry_queue WHERE resolved=0"
        ).fetchone()[0]
        if retry_q > 0:
            issues.append(f"WARNING: {retry_q} unresolved entries in data_retry_queue")

        conn.close()
    except Exception as exc:
        issues.append(f"ERROR connecting to DB: {exc}")
    return issues


def _check_cache() -> list[str]:
    issues: list[str] = []
    if not _CACHE_DIR.exists():
        return ["WARNING: cache dir missing"]

    files = list(_CACHE_DIR.glob("*.json"))
    if not files:
        issues.append("WARNING: no cache files found; provider calls may be hitting rate limits")
    else:
        print(f"  [Cache] {len(files)} cached files OK")
    return issues


def _check_dashboard() -> list[str]:
    issues: list[str] = []
    if not _DOCS.exists():
        return ["WARNING: dashboard_data.json missing; dashboard will be blank"]

    try:
        data = json.loads(_DOCS.read_text(encoding="utf-8"))
        top10 = data.get("top10", [])
        if not top10:
            issues.append("WARNING: dashboard top10 is empty")
        else:
            print(f"  [Dashboard] top10 has {len(top10)} entries OK")
    except Exception as exc:
        issues.append(f"ERROR reading dashboard JSON: {exc}")
    return issues


def _check_delivery_log() -> list[str]:
    issues: list[str] = []
    if not _DB.exists():
        return issues

    try:
        conn = sqlite3.connect(_DB)
        today = str(date.today())
        rows = conn.execute(
            "SELECT task, status, detail FROM delivery_log WHERE delivered_at LIKE ?",
            (f"{today}%",),
        ).fetchall()
        for task, status, detail in rows:
            if status != "ok":
                issues.append(f"DELIVERY FAIL: {task}: {status} | {detail}")
            else:
                print(f"  [Delivery] {task}: {status} OK")
        conn.close()
    except Exception:
        pass
    return issues


def _suggestions(all_issues: list[str]) -> list[str]:
    suggestions: list[str] = []
    if any("retry_queue" in issue for issue in all_issues):
        suggestions.append(
            "Some provider calls failed. Check API rate limits "
            "(SEC: 10 req/s, yfinance: no hard limit but avoid burst)."
        )
    if any("No scores" in issue for issue in all_issues):
        suggestions.append("Scoring pipeline may have failed. Run: python main.py and check traceback.")
    if any("dashboard" in issue.lower() for issue in all_issues):
        suggestions.append("Dashboard JSON missing. Ensure write_dashboard_json() is called in main.py.")
    if not all_issues:
        suggestions.append("All checks passed. Consider adding more symbols to config.yaml for broader coverage.")
        suggestions.append("Review watch_signals outcomes weekly to calibrate scoring weights.")
    return suggestions


def main() -> int:
    print("=" * 60)
    print(f"POST-UPDATE HEALTH CHECK - {date.today()}")
    print("=" * 60)

    all_issues: list[str] = []
    all_issues += _check_db()
    all_issues += _check_cache()
    all_issues += _check_dashboard()
    all_issues += _check_delivery_log()

    if all_issues:
        print("\nISSUES FOUND:")
        for issue in all_issues:
            print(f"  {issue}")
    else:
        print("\nNo issues found.")

    print("\nOPTIMIZATION SUGGESTIONS:")
    for suggestion in _suggestions(all_issues):
        print(f"  {suggestion}")

    print("=" * 60)
    return 1 if any("CRITICAL" in issue or "ERROR" in issue for issue in all_issues) else 0


if __name__ == "__main__":
    sys.exit(main())
