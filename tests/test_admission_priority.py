"""The AI review budget must be aimed at admissible candidates.

2026-08-03: the board held 24 candidates, admission requires a same-day AI
verdict, and only 5 reviews are affordable per run. Those 5 were taken in raw
scan order — which is by v2 score, so fresh high-scoring newcomers (streak 0,
not yet admissible) crowded out the names that had served their two weeks.
Result: 10 fully-qualified candidates skipped as "no-review", pool frozen at 35
against a 60-90 target, and every validation group's distinct-symbol count
capped along with it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import scripts.pool_autopilot as ap


@pytest.fixture
def streak_file(tmp_path, monkeypatch):
    def _write(mapping):
        p = tmp_path / "streak.json"
        p.write_text(json.dumps(mapping), encoding="utf-8")
        monkeypatch.setattr(ap, "_STREAK", p)
        return p
    return _write


def _scan(*rows):
    return {"candidates": [
        {"symbol": s, "score_v2": v, "in_watchlist": w} for s, v, w in rows
    ]}


def test_qualified_candidates_outrank_higher_scoring_newcomers(streak_file):
    """The 8/3 shape: newcomers top the board on score but cannot be admitted."""
    streak_file({"OLD1": 1, "OLD2": 1})          # +1 this run -> meets the 2-week bar
    scan = _scan(("NEW1", 99, False), ("NEW2", 98, False),
                 ("OLD1", 70, False), ("OLD2", 69, False))

    order = ap.admission_priority(scan)

    assert order[:2] == ["OLD1", "OLD2"], "admissible names must come first"
    assert set(order[2:]) == {"NEW1", "NEW2"}


def test_score_still_breaks_ties_within_the_eligible_set(streak_file):
    streak_file({"A": 3, "B": 3})
    order = ap.admission_priority(_scan(("A", 80, False), ("B", 95, False)))
    assert order == ["B", "A"]


def test_watchlist_members_are_not_review_candidates(streak_file):
    streak_file({"IN": 5})
    order = ap.admission_priority(_scan(("IN", 99, True), ("OUT", 50, False)))
    assert order == ["OUT"]


def test_missing_streak_file_degrades_to_score_order(streak_file, tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "_STREAK", tmp_path / "absent.json")
    order = ap.admission_priority(_scan(("A", 50, False), ("B", 90, False)))
    assert order == ["B", "A"]


def test_no_scan_yields_no_priority():
    assert ap.admission_priority(None) == []
    assert ap.admission_priority({}) == []
