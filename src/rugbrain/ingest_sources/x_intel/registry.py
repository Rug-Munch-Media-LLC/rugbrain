"""``config/x_registry.json`` → x_intel rotation (top-500 X account registry).

One source of registry handles for the existing x_intel path — NO separate
pipeline: ``registry_handles`` feeds ``pipeline._select_accounts`` so registry
accounts ride the same fetch → dedupe → embed → ``news_v2`` flow, and
``registry_entry`` tags a doc with the account's kind / role / trust.

Fail-open: a missing or malformed file yields an empty registry and the tier
rotation keeps working exactly as before. Handles whose kind is
``scam_caller`` are EXCLUDED from the rotation: they are negative-signal
accounts (trust 0) kept for attribution education, never content sources.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("rugbrain.x_intel")

DEFAULT_PATH = "config/x_registry.json"
#: Kinds that may be ingested. ``scam_caller`` is negative-signal (never posts).
SOURCE_KINDS: tuple[str, ...] = ("news", "kol", "security", "analyst")

_REG_CACHE: dict[str, Any] = {}


def registry_path(path: str | None = None) -> Path:
    """Resolve the registry file: arg > ``XINTEL_REGISTRY_PATH`` > default."""
    explicit = path or os.getenv("XINTEL_REGISTRY_PATH", "").strip() or DEFAULT_PATH
    resolved = Path(explicit)
    return resolved if resolved.is_absolute() else Path.cwd() / resolved


def load_registry(path: str | None = None, *, refresh: bool = False) -> dict[str, Any]:
    """The whole registry payload, cached; fail-open to an empty shape."""
    key = str(path or "")
    if refresh or key not in _REG_CACHE:
        _REG_CACHE[key] = _read(registry_path(key))
    return _REG_CACHE[key]


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("xintel_registry:load_failed path=%s err=%s", path, str(exc)[:120])
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("entries"), list) else {}


def registry_entries(path: str | None = None) -> list[dict[str, Any]]:
    """Ingestible entries: kinds in ``SOURCE_KINDS``, real handles only."""
    return [
        entry
        for entry in load_registry(path).get("entries", [])
        if isinstance(entry, dict)
        and entry.get("kind") in SOURCE_KINDS
        and str(entry.get("handle") or "").strip()
    ]


def registry_handles(path: str | None = None) -> list[str]:
    """Handles eligible for the x_intel rotation (deduped, first wins)."""
    out: list[str] = []
    seen: set[str] = set()
    for entry in registry_entries(path):
        handle = entry["handle"].strip().lstrip("@")
        low = handle.lower()
        if low not in seen:
            seen.add(low)
            out.append(handle)
    return out


def registry_entry(handle: str, path: str | None = None) -> dict[str, Any] | None:
    """The registry row for *handle* (case-insensitive), or None."""
    wanted = str(handle or "").strip().lstrip("@").lower()
    for entry in load_registry(path).get("entries", []):
        if str(entry.get("handle") or "").strip().lstrip("@").lower() == wanted:
            return entry
    return None


__all__ = [
    "DEFAULT_PATH",
    "SOURCE_KINDS",
    "registry_entries",
    "registry_entry",
    "registry_handles",
    "registry_path",
]
