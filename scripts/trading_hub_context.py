"""
決策前讀回 Trading Knowledge Hub 的美股相關知識，避免重複犯錯。
鏡像自 MT5 mt5_deepseek_demo_stack/trading_hub_context.py，domain 改為 us_stock。

用法：
    python scripts/trading_hub_context.py            # 產生並印出 context
    from scripts.trading_hub_context import load_context   # 決策流程中讀取
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HUB_ROOT = Path("C:/Users/User/trading_knowledge_hub")
CONTEXT_FILE = ROOT / "data" / "trading_hub_context.json"
DOMAIN = "us_stock"
DOMAIN_STATUSES = {"adopted", "live_supported", "backtest_supported"}
GENERAL_STATUSES = {"adopted", "live_supported"}


def now_local() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_hub_rows(hub_root: Path = DEFAULT_HUB_ROOT) -> list[dict[str, Any]]:
    if not hub_root.exists():
        raise FileNotFoundError(f"hub root not found: {hub_root}")
    sys.path.insert(0, str(hub_root))
    from app.storage import list_knowledge  # type: ignore

    rows: list[dict[str, Any]] = []
    for status in sorted(DOMAIN_STATUSES):
        rows.extend(list_knowledge(domain=DOMAIN, status=status))
    for status in sorted(GENERAL_STATUSES):
        rows.extend(list_knowledge(domain="general", status=status))
    seen: set[str] = set()
    unique = []
    for r in rows:
        rid = str(r.get("id"))
        if rid in seen:
            continue
        seen.add(rid)
        unique.append(r)
    unique.sort(key=lambda item: (-float(item.get("confidence") or 0), str(item.get("topic"))))
    return unique


def build_context(hub_root: Path = DEFAULT_HUB_ROOT, limit: int = 40) -> dict[str, Any]:
    rows = load_hub_rows(hub_root)
    compact = [
        {
            "id": it.get("id"), "topic": it.get("topic"), "claim": it.get("claim"),
            "domain": it.get("domain"), "status": it.get("status"),
            "confidence": it.get("confidence"), "evidence": it.get("evidence"),
            "source_ref": it.get("source_ref"), "tags": it.get("tags") or [],
        }
        for it in rows[:limit]
    ]
    context = {
        "ok": True, "generated_at": now_local(), "hub_root": str(hub_root),
        "domain": DOMAIN, "count": len(rows), "used_count": len(compact), "rows": compact,
    }
    CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONTEXT_FILE.write_text(json.dumps(context, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return context


def load_context() -> dict[str, Any]:
    """決策流程呼叫此函式取得 Hub 知識（無檔時回傳空 rows，不拋例外）。"""
    if not CONTEXT_FILE.exists():
        return {"ok": False, "error": "context not generated", "rows": []}
    try:
        return json.loads(CONTEXT_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "rows": []}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read validated us_stock + general knowledge from Trading Knowledge Hub.")
    parser.add_argument("--hub-root", default=str(DEFAULT_HUB_ROOT))
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    context = build_context(Path(args.hub_root), args.limit)
    print(json.dumps(context, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
