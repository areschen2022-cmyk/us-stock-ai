from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).parent.parent
_CONFIG_PATH = _ROOT / "config.yaml"

_cfg: dict[str, Any] | None = None


def get_config() -> dict[str, Any]:
    global _cfg
    if _cfg is None:
        with open(_CONFIG_PATH) as f:
            _cfg = yaml.safe_load(f)
    return _cfg


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)
