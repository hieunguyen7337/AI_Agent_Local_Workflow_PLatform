"""Loads and caches the python_tool allowlist from python_tools.yaml at the repo root."""
from __future__ import annotations

from pathlib import Path

import yaml

_ALLOWLIST_PATH = Path(__file__).parent.parent.parent / "python_tools.yaml"
_cache: frozenset[str] | None = None


def load_allowlist() -> frozenset[str]:
    global _cache
    if _cache is not None:
        return _cache
    _cache = _load()
    return _cache


def _load() -> frozenset[str]:
    if not _ALLOWLIST_PATH.exists():
        return frozenset()
    try:
        raw = yaml.safe_load(_ALLOWLIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return frozenset()
    if not isinstance(raw, dict):
        return frozenset()
    entries = raw.get("allowed_callables", [])
    if not isinstance(entries, list):
        return frozenset()
    return frozenset(str(e) for e in entries if isinstance(e, str) and e.strip())


def _clear_cache() -> None:
    global _cache
    _cache = None
