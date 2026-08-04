"""Pool autopilot — unattended watchlist add/remove (no human, no Claude).

Runs Mondays in CI after the daily pipeline (scan + AI reviews done). Rules:

REMOVE (自動退池):
  - every pool-exit candidate from market_scan.json (v2 < 40 for 4 straight
    weekly checks)
  - caps: max 8 removals/week; never shrink the pool below 30

ADMIT (自動入池):
  - candidate must be S-grade on the scan board TWO consecutive weeks
    (data/candidate_streak.json — the LQDA 98→62-in-a-week lesson: one hot
    week is not persistence)
  - AND today's DeepSeek review (ai_council_reviews) is Buy or Hold — a
    missing review or Avoid blocks admission
  - AND earnings >= 7 calendar days away (yfinance)
  - caps: max 3 admits/week, max 2 per sector/week, pool ceiling 90

All changes are appended to data/watchlist_changes.jsonl with reasons and
summarized in data/pool_changes.json (dashboard + morning brief show them,
so unattended operation stays transparent).

Usage:
  python scripts/pool_autopilot.py                 # dry-run report
  python scripts/pool_autopilot.py --apply         # Mondays only (CI)
  python scripts/pool_autopilot.py --apply --force # ignore weekday gate
  python scripts/pool_autopilot.py --apply --remove SYMS...  # explicit batch
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.atomic_io import atomic_write_text

_CONFIG = _REPO_ROOT / "config.yaml"
_CHANGES_LOG = _REPO_ROOT / "data" / "watchlist_changes.jsonl"
_POOL_CHANGES = _REPO_ROOT / "data" / "pool_changes.json"
_STREAK = _REPO_ROOT / "data" / "candidate_streak.json"
_SCAN = _REPO_ROOT / "data" / "market_scan.json"
_DB = _REPO_ROOT / "data" / "us_stock_ai.sqlite3"

MAX_REMOVE_PER_WEEK = 8
MAX_ADMIT_PER_WEEK = 3
MAX_ADMIT_PER_SECTOR = 2
POOL_FLOOR = 30
POOL_CEILING = 90
MIN_STREAK_WEEKS = 2


def admission_priority(scan: dict | None) -> list[str]:
    """Scan candidates ordered by how close they are to actually being admitted.

    The AI review budget is small (5 scan reviews/run) while the candidate board
    runs to ~24, and admission REQUIRES a same-day AI verdict. Reviewing the
    board in raw scan order therefore spent the whole budget on names that were
    not admissible anyway: on 2026-08-03, 11 candidates had cleared the streak
    threshold, only 1 of them fell inside the reviewed top-5 (and that one was
    then blocked by earnings), so 10 fully-qualified names were skipped as
    "no-review" and the pool sat at 35 against a 60-90 target.

    Ordering here rather than in the council keeps the governance rules in the
    governance module — the council should not have to know what makes a
    candidate admissible.
    """
    if not scan:
        return []
    streak: dict = {}
    if _STREAK.exists():
        try:
            streak = json.loads(_STREAK.read_text(encoding="utf-8"))
        except Exception:
            streak = {}
    board = [c for c in scan.get("candidates", []) if not c.get("in_watchlist")]

    def rank(c: dict) -> tuple:
        sym = c.get("symbol", "")
        # +1 because the autopilot increments the streak on the same run that
        # reads the review — eligibility is judged on next week's value.
        eligible = streak.get(sym, 0) + 1 >= MIN_STREAK_WEEKS
        return (eligible, c.get("score_v2") or 0)

    return [c["symbol"] for c in sorted(board, key=rank, reverse=True) if c.get("symbol")]


def _pool() -> list[str]:
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8")).get("symbols", [])


def _log_change(action: str, symbol: str, reason: str, source: str) -> None:
    _CHANGES_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _CHANGES_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "date": str(date.today()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action, "symbol": symbol, "reason": reason, "source": source,
        }, ensure_ascii=False) + "\n")


def _edit_symbols(add: list[str], remove: list[str]) -> list[str]:
    """Text-surgical config.yaml edit preserving comments/format."""
    text = _CONFIG.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    sym_line = re.compile(r"^(\s*)-\s+(\S+)\s*$")
    out, in_block, indent, last_idx = [], False, "  ", None
    for line in lines:
        if re.match(r"^symbols\s*:", line):
            in_block = True
            out.append(line)
            continue
        if in_block:
            m = sym_line.match(line)
            if m:
                indent = m.group(1)
                if m.group(2).upper() in remove:
                    continue  # drop the removed symbol's line
                out.append(line)
                last_idx = len(out) - 1
                continue
            if line.strip() and not line.startswith((" ", "\t", "#")):
                in_block = False
        out.append(line)
    if add and last_idx is not None:
        out.insert(last_idx + 1, "".join(f"{indent}- {s}\n" for s in add))
    atomic_write_text(_CONFIG, "".join(out))
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8")).get("symbols", [])


def _todays_ai_actions() -> dict[str, str]:
    try:
        conn = sqlite3.connect(str(_DB))
        rows = conn.execute(
            "SELECT symbol, consensus_action FROM ai_council_reviews WHERE review_date=?",
            (str(date.today()),)).fetchall()
        return {r[0]: (r[1] or "") for r in rows}
    except Exception:
        return {}


def _earnings_days(symbol: str) -> int | None:
    try:
        import yfinance as yf
        cal = yf.Ticker(symbol).calendar
        ed = None
        if isinstance(cal, dict):
            v = cal.get("Earnings Date")
            ed = v[0] if isinstance(v, (list, tuple)) and v else v
        if ed is None:
            return None
        if hasattr(ed, "date") and not isinstance(ed, date):
            ed = ed.date()
        return (ed - date.today()).days if isinstance(ed, date) else None
    except Exception:
        return None


def _sector(symbol: str) -> str:
    try:
        import yfinance as yf
        return (yf.Ticker(symbol).info or {}).get("sector") or "?"
    except Exception:
        return "?"


def run(apply: bool, force_remove: list[str] | None = None) -> dict:
    scan = json.loads(_SCAN.read_text(encoding="utf-8")) if _SCAN.exists() else {}
    pool = _pool()
    result = {"date": str(date.today()), "added": [], "removed": [], "notes": []}

    # ── REMOVALS ──────────────────────────────────────────────────────────
    if force_remove:
        to_remove = [s.upper() for s in force_remove if s.upper() in pool]
    else:
        exit_cands = [c["symbol"] for c in (scan.get("pool_exit") or {}).get("candidates", [])
                      if c.get("symbol") in pool]
        to_remove = exit_cands[:MAX_REMOVE_PER_WEEK]
    if len(pool) - len(to_remove) < POOL_FLOOR:
        keep_n = len(pool) - POOL_FLOOR
        result["notes"].append(f"pool floor {POOL_FLOOR}: removals trimmed to {keep_n}")
        to_remove = to_remove[:max(0, keep_n)]

    # ── ADMISSIONS (streak + AI + earnings + sector caps) ─────────────────
    streak: dict = {}
    if _STREAK.exists():
        try:
            streak = json.loads(_STREAK.read_text(encoding="utf-8"))
        except Exception:
            streak = {}
    board = [c for c in scan.get("candidates", []) if not c.get("in_watchlist")]
    board_syms = {c["symbol"] for c in board}
    new_streak = {s: (streak.get(s, 0) + 1) for s in board_syms}

    ai = _todays_ai_actions()
    admits, sector_count = [], {}
    projected = len(pool) - len(to_remove)
    for c in sorted(board, key=lambda x: -x.get("score_v2", 0)):
        if len(admits) >= MAX_ADMIT_PER_WEEK or projected + len(admits) >= POOL_CEILING:
            break
        sym = c["symbol"]
        wk = new_streak.get(sym, 0)
        if wk < MIN_STREAK_WEEKS:
            # Logged, not silent: this branch skipped the board's top names on
            # 2026-08-03 leaving no trace, which made "why did the pool not
            # grow?" take a full investigation. Every skip reason is evidence.
            result["notes"].append(f"{sym}: streak {wk}/{MIN_STREAK_WEEKS} → skip")
            continue
        act = ai.get(sym)
        if act not in ("Buy", "Hold"):
            result["notes"].append(f"{sym}: AI={act or 'no-review'} → skip")
            continue
        ed = _earnings_days(sym)
        if ed is not None and ed < 7:
            result["notes"].append(f"{sym}: earnings in {ed}d → skip")
            continue
        sec = _sector(sym)
        if sector_count.get(sec, 0) >= MAX_ADMIT_PER_SECTOR:
            result["notes"].append(f"{sym}: sector cap ({sec}) → skip")
            continue
        sector_count[sec] = sector_count.get(sec, 0) + 1
        admits.append({"symbol": sym, "v2": c.get("score_v2"), "sector": sec,
                       "streak": new_streak.get(sym), "ai": act, "earnings_days": ed})

    result["removed"] = to_remove
    result["added"] = admits

    if apply:
        add_syms = [a["symbol"] for a in admits]
        final = _edit_symbols(add_syms, [s.upper() for s in to_remove])
        for s in to_remove:
            _log_change("remove", s, "autopilot: v2<40 連續4週退池規則" if not force_remove
                        else "autopilot: 使用者核准之批次退池(v2<40連續4週)", "pool_autopilot")
        for a in admits:
            _log_change("add", a["symbol"],
                        f"autopilot: 連續{a['streak']}週S級 v2={a['v2']} AI={a['ai']} "
                        f"財報{a['earnings_days']}天 產業={a['sector']}", "pool_autopilot")
        # streak resets for admitted symbols; persists for the rest
        for a in admits:
            new_streak.pop(a["symbol"], None)
        atomic_write_text(_STREAK, json.dumps(new_streak, ensure_ascii=False, indent=1))
        atomic_write_text(_POOL_CHANGES, json.dumps(result, ensure_ascii=False, indent=1))
        result["pool_size"] = len(final)
    else:
        result["pool_size"] = len(pool) - len(to_remove) + len(admits)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--force", action="store_true", help="ignore Monday gate")
    p.add_argument("--remove", nargs="*", help="explicit removal batch (user-approved)")
    a = p.parse_args()
    if a.apply and not a.force and not a.remove and date.today().weekday() != 0:
        print("[Autopilot] Not Monday — skipping (use --force)")
        sys.exit(0)
    res = run(apply=a.apply, force_remove=a.remove)
    print(json.dumps(res, ensure_ascii=False, indent=1))
