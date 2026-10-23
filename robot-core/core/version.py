"""The release this runs from (version.json, written by the robot's release.py), or dev."""

from __future__ import annotations

import json
from pathlib import Path

_FILE = Path(__file__).resolve().parents[1] / "version.json"


def version() -> dict:
    try:
        return json.loads(_FILE.read_text())
    except (OSError, ValueError):
        return {"rev": "dev", "dirty": None}
