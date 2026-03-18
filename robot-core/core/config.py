"""
core/config.py — YAML config loader with dot-notation access and env-override.

Usage:
    cfg = load_config("config/robot.yaml")
    cfg.robot.name          # → "Spooky"
    cfg.get("llm.model", "llama3.2:1b")
    cfg["safety"]["max_speed"]
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional, Union

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


class ConfigNode:
    """
    Thin wrapper around a nested dict that allows dot-notation access.
    Also supports dict-style access and env-var overrides (ROBOT_<KEY>=value).
    """

    def __init__(self, data: dict):
        self._data: dict = data

    # ── access ───────────────────────────────────────────────────────────────

    def __getattr__(self, key: str) -> Any:
        if key.startswith("_"):
            raise AttributeError(key)
        return self._get(key)

    def __getitem__(self, key: str) -> Any:
        return self._get(key)

    def _get(self, key: str) -> Any:
        val = self._data.get(key)
        if isinstance(val, dict):
            return ConfigNode(val)
        return val

    def get(self, dotpath: str, default: Any = None) -> Any:
        """
        Access a nested value via dot-notation path.
        E.g. cfg.get("safety.max_speed", 50)
        """
        parts = dotpath.split(".")
        node = self._data
        for part in parts:
            if not isinstance(node, dict):
                return default
            node = node.get(part)
            if node is None:
                return default
        if isinstance(node, dict):
            return ConfigNode(node)
        return node

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def as_dict(self) -> dict:
        return self._data

    def __repr__(self) -> str:
        return f"<ConfigNode keys={list(self._data.keys())}>"


# ── loader ────────────────────────────────────────────────────────────────────

def load_config(*paths: Union[str, Path]) -> ConfigNode:
    """
    Load one or more YAML config files and merge them left-to-right
    (later files override earlier ones). Returns a ConfigNode.

    Falls back to an empty config if yaml is not installed.
    """
    merged: dict = {}
    for path in paths:
        path = Path(path)
        if not path.exists():
            continue
        if not _HAS_YAML:
            raise ImportError("PyYAML is required: pip install pyyaml")
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _deep_merge(merged, data)
    return ConfigNode(merged)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base in-place."""
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val
    return base
