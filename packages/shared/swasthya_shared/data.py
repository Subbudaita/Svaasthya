"""Locate the repository's ``data/`` directory.

Everything globally true (guideline baselines, nutrition tables, lab reference
ranges, phase templates, anthropometric thresholds) lives as files under
``data/``. Nothing user-specific does — that lives in database rows.
"""

import json
import os
from functools import cache
from pathlib import Path
from typing import Any

_REPO_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def data_dir() -> Path:
    """Return the data directory, overridable with ``SWASTHYA_DATA_DIR``."""
    override = os.environ.get("SWASTHYA_DATA_DIR")
    path = Path(override) if override else _REPO_DATA_DIR
    if not path.is_dir():
        raise FileNotFoundError(f"Swasthya data directory not found: {path}")
    return path


@cache
def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def pillars() -> list[dict[str, str]]:
    """The six consistency pillars, from ``data/pillars.json``."""
    return _load_json(data_dir() / "pillars.json")["pillars"]


def pillar_keys() -> frozenset[str]:
    return frozenset(p["key"] for p in pillars())
