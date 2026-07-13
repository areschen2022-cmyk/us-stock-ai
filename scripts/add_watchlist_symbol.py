"""Formal watchlist-addition flow (候補→watchlist 入池).

Adds symbols to config.yaml's watchlist AND records why in
data/watchlist_changes.jsonl — so future postmortems can attribute
performance to the pool-change decision (kp_us_score_v2_sp500_validation:
pool selection is the dominant alpha source, so pool changes deserve the
same audit trail as trades).

Text-based insertion (not yaml round-trip) so comments/format in config.yaml
survive untouched.

Usage:
    python scripts/add_watchlist_symbol.py FTNT DELL --reason "全市場v2掃描S級+AI複核Buy(0.85)"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parent.parent
_CONFIG = _REPO_ROOT / "config.yaml"
_LOG = _REPO_ROOT / "data" / "watchlist_changes.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="+", help="ticker(s) to add")
    parser.add_argument("--reason", required=True, help="why this symbol enters the pool")
    parser.add_argument("--source", default="market_scan", help="candidate source tag")
    args = parser.parse_args()

    text = _CONFIG.read_text(encoding="utf-8")
    current = set(yaml.safe_load(text).get("symbols", []))

    to_add = []
    for sym in [s.upper().strip() for s in args.symbols]:
        if sym in current:
            print(f"[Watchlist] {sym} already present — skipped")
        else:
            to_add.append(sym)
    if not to_add:
        print("[Watchlist] nothing to add")
        return 0

    # insert right after the last existing "  - SYM" line of the symbols block
    lines = text.splitlines(keepends=True)
    sym_line = re.compile(r"^(\s*)-\s+\S+\s*$")
    in_block, last_idx, indent = False, None, "  "
    for i, line in enumerate(lines):
        if re.match(r"^symbols\s*:", line):
            in_block = True
            continue
        if in_block:
            m = sym_line.match(line)
            if m:
                last_idx, indent = i, m.group(1)
            elif line.strip() and not line.startswith((" ", "\t", "#")):
                break  # left the block
    if last_idx is None:
        print("[Watchlist] ERROR: could not locate symbols block in config.yaml")
        return 1

    insertion = "".join(f"{indent}- {s}\n" for s in to_add)
    lines.insert(last_idx + 1, insertion)
    _CONFIG.write_text("".join(lines), encoding="utf-8")

    # sanity: file still parses and contains the new symbols
    reloaded = set(yaml.safe_load(_CONFIG.read_text(encoding="utf-8")).get("symbols", []))
    missing = [s for s in to_add if s not in reloaded]
    if missing:
        print(f"[Watchlist] ERROR: post-write validation failed for {missing}")
        return 1

    _LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LOG.open("a", encoding="utf-8") as f:
        for s in to_add:
            f.write(json.dumps({
                "date": str(date.today()),
                "ts": datetime.now(timezone.utc).isoformat(),
                "action": "add",
                "symbol": s,
                "reason": args.reason,
                "source": args.source,
            }, ensure_ascii=False) + "\n")

    print(f"[Watchlist] added {', '.join(to_add)} (now {len(reloaded)} symbols); "
          f"logged to {_LOG.name}")
    print("[Watchlist] Note: new symbols enter scoring on the next daily run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
