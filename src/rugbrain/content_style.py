"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Canonical content style — loader for ``config/content_style.json``.

ONE source of truth for voice, tense, spacing, number formats, citation
format, disclaimers and length budgets. Everything that renders text
(prompts, the Daily Rug generator, the style linter) reads this file; none
of them carries its own copy of the rules.

The config lives at the repository root, not inside the package, because it
is an editorial artefact reviewed by humans. ``RUGBRAIN_CONFIG_DIR`` points
the loader at another directory (containers copy ``config/`` to ``/app``).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

CONFIG_ENV = "RUGBRAIN_CONFIG_DIR"
STYLE_FILENAME = "content_style.json"


def config_dir() -> Path:
    """Directory holding the editorial config files."""
    override = os.getenv(CONFIG_ENV, "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "config"


@lru_cache(maxsize=4)
def _read(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: content style must be a JSON object")
    return data


def load_style(path: str | Path | None = None) -> dict[str, Any]:
    """Return the canonical style document.

    The returned mapping is shared and must be treated as read-only; copy it
    before mutating in a test.
    """
    target = Path(path) if path is not None else config_dir() / STYLE_FILENAME
    return _read(str(target))


def budget(surface: str, style: dict[str, Any] | None = None) -> int:
    """Character budget for one surface. Unknown surfaces raise."""
    budgets = (style or load_style())["length_budgets"]
    if surface not in budgets:
        raise KeyError(f"no length budget for surface {surface!r}")
    return int(budgets[surface])


def banned_phrases(style: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Hype phrases that must never appear in published copy."""
    rules = style or load_style()
    return tuple(str(p).lower() for p in rules["voice"]["banned_phrases"])


def banned_patterns(style: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Price-prediction regexes that must never appear in published copy."""
    rules = style or load_style()
    return tuple(str(p) for p in rules["voice"]["banned_patterns"])


def emoji_allowlist(style: dict[str, Any] | None = None) -> frozenset[str]:
    """The only emoji a renderer may emit."""
    rules = style or load_style()
    return frozenset(str(e) for e in rules["emoji"]["allowed"])


def acronym_allowlist(style: dict[str, Any] | None = None) -> frozenset[str]:
    """Acronyms that may appear in full uppercase in body copy."""
    rules = style or load_style()
    return frozenset(str(a) for a in rules["capitalization"]["acronyms_allowed"])
