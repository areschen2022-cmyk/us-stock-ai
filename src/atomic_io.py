"""Atomic file writes for published artifacts.

Codex audit #9 (2026-07-19): dashboard/performance/scan JSONs were written
with plain write_text — the auto-commit watcher, GitHub Pages deploy, or the
morning-brief reader could observe a truncated file mid-write. Write to a
temp file in the SAME directory (same filesystem) and os.replace(), which is
atomic on both POSIX and Windows.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path | str, content: str, encoding: str = "utf-8") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
