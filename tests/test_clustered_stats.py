"""t-stats must be clustered by symbol, not computed per signal.

2026-07-31: this system re-signals the same names daily, so one stock supplies
many rows that move together. Treating them as independent inflated the exit
decision's t from 0.96 to 6.63 and turned several non-results into "significant"
findings. `_agg` now reports both; the clustered one is what decisions use.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from monthend_review import _agg


def test_repeated_symbol_does_not_inflate_significance():
    """One stock measured 20 times is one bet, not 20. The naive t should look
    impressive and the clustered t should refuse to."""
    vals = [3.0, 3.1, 2.9, 3.0] * 5          # 20 readings
    symbols = ["AAA"] * 20                    # ...all the same stock

    out = _agg(vals, symbols)

    assert out["n"] == 20
    assert out["n_symbols"] == 1
    assert out["t"] > 20            # naive: looks overwhelming
    assert out["t_clustered"] is None   # one cluster cannot support a t at all


def test_clustered_t_tracks_distinct_bets():
    """Same values spread over many names keeps its significance — the penalty
    is for repetition, not for sample size as such."""
    vals = [3.0, 3.1, 2.9, 3.0] * 5
    symbols = [f"S{i:02d}" for i in range(20)]

    out = _agg(vals, symbols)

    assert out["n_symbols"] == 20
    assert out["t_clustered"] is not None
    assert out["t_clustered"] > 20


def test_clustering_can_reverse_the_sign():
    """The 避免追高 case: a big positive mean carried by one repeated winner,
    while most distinct names were negative."""
    vals = [10.0] * 9 + [-2.0, -3.0, -1.0]
    symbols = ["WIN"] * 9 + ["A", "B", "C"]

    out = _agg(vals, symbols)

    assert out["avg"] > 0             # per-signal mean is positive
    assert out["avg_clustered"] < out["avg"]
    assert out["n_symbols"] == 4


def test_without_symbols_no_clustered_claim_is_made():
    """Callers that cannot supply symbols must get None, never a fabricated
    clustered value that would read as if it had been corrected."""
    out = _agg([1.0, 2.0, 3.0])

    assert out["t"] is not None
    assert out["t_clustered"] is None
    assert out["n_symbols"] is None
