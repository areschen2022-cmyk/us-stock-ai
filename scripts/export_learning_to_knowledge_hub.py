"""Weekly export of outcome learning to Trading Knowledge Hub MCP."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

# Running this file directly (`python scripts/export_learning_to_knowledge_hub.py`)
# only puts scripts/ on sys.path, not the repo root — so `from src...` below
# raised ModuleNotFoundError on every CI run (masked by continue-on-error).
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_MCP_SERVER = Path("C:/Users/User/trading_knowledge_hub/mcp_server.py")
_STORE_PATH = Path(__file__).parent.parent / "data" / "us_stock_ai.sqlite3"


def _call_mcp(tool: str, args: dict) -> dict | None:
    if not _MCP_SERVER.exists():
        print(f"[KnowledgeHub] MCP server not found at {_MCP_SERVER}")
        return None
    payload = json.dumps({"tool": tool, "args": args})
    try:
        result = subprocess.run(
            [sys.executable, str(_MCP_SERVER), "--call", payload],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return json.loads(result.stdout.strip())
        print(f"[KnowledgeHub] MCP error: {result.stderr[:200]}")
        return None
    except Exception as exc:
        print(f"[KnowledgeHub] Exception calling MCP: {exc}")
        return None


def _build_claim(row: dict) -> str:
    symbol = row["symbol"]
    score = row["score"]
    grade = row["grade"]
    outcome = row.get("outcome", "unknown")
    ret5 = row.get("return_5d")
    ret10 = row.get("return_10d")
    r5 = f"{ret5:+.1f}%" if ret5 is not None else "N/A"
    r10 = f"{ret10:+.1f}%" if ret10 is not None else "N/A"
    return (
        f"US Stock AI signal: {symbol} scored {score} ({grade}) on {row['signal_date']}. "
        f"5d return: {r5}, 10d return: {r10}. Outcome: {outcome}."
    )


def export_weekly() -> int:
    from src.storage.sqlite_store import SQLiteStore
    store = SQLiteStore(path=_STORE_PATH)
    rows = store.get_unexported_outcomes(limit=30)

    if not rows:
        print("[KnowledgeHub] No new outcomes to export.")
        return 0

    exported = 0
    skipped = 0
    for row in rows:
        claim = _build_claim(row)
        topic = f"US Stock Signal: {row['symbol']} {row['signal_date']}"
        result = _call_mcp("upsert_knowledge", {
            "topic": topic,
            "claim": claim,
            "domain": "us_stock",
            "tags": [row["symbol"], row.get("grade", ""), "signal_outcome"],
        })
        if result is None:
            print(f"[KnowledgeHub] Skipped {row['symbol']} {row['signal_date']} (MCP unavailable)")
            skipped += 1
            continue
        hub_id = result.get("id", "")
        store.save_knowledge_export(
            topic=topic,
            claim=claim,
            signal_date=row["signal_date"],
            hub_id=hub_id,
        )
        print(f"[KnowledgeHub] Exported {row['symbol']} {row['signal_date']} → {hub_id or 'no id'}")
        exported += 1

    print(f"[KnowledgeHub] Done. Exported {exported}, skipped {skipped} (MCP unavailable).")
    return exported


if __name__ == "__main__":
    export_weekly()
