"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Prompt pack — one system prompt, one template per surface.

Templates live in ``config/content_prompts.json`` so a prompt change is a data
change with a reviewable diff. The style half of every prompt is injected from
``config/content_style.json``: no template restates the voice rules, so the two
files cannot drift into competing instructions.

Free models get a flat, numbered contract and an explicit output format; the
caller sends the rendered string as the user message and the system prompt as
the system message.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any

from rugbrain.content_style import config_dir, load_style

PROMPTS_FILENAME = "content_prompts.json"

#: Surfaces that must carry a disclaimer in their prompt.
DISCLAIMER_SURFACES: dict[str, str] = {
    "daily_rug": "daily_rug",
    "feed_longform": "daily_rug",
    "alert": "alert",
}


@lru_cache(maxsize=4)
def _read(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or "surfaces" not in data:
        raise ValueError(f"{path}: prompt pack has no surfaces")
    return data


def load_prompts(path: str | Path | None = None) -> dict[str, Any]:
    """Return the canonical prompt pack (shared, treat as read-only)."""
    target = Path(path) if path is not None else config_dir() / PROMPTS_FILENAME
    return _read(str(target))


def surfaces(pack: dict[str, Any] | None = None) -> tuple[str, ...]:
    """Every surface the pack can render, sorted for stable output."""
    return tuple(sorted((pack or load_prompts())["surfaces"]))


def _budget_key(pack: dict[str, Any], surface: str) -> str:
    return str(pack["surfaces"][surface]["budget"])


def style_block(surface: str, style: dict[str, Any] | None = None) -> str:
    """The style half of the prompt, rendered from the canonical config."""
    rules = style or load_style()
    voice = rules["voice"]
    numbers = rules["numbers"]
    lines = [
        f"REGISTER: {voice['register']}. {voice['person']}.",
        "ARC: " + "; ".join(voice["arc"]) + ".",
        f"TICKERS: {rules['capitalization']['tickers']}.",
        f"NUMBERS: {numbers['crypto_amounts']}; {numbers['percent']}; {numbers['usd']}.",
        f"SPACING: {rules['spacing']['blank_lines_between_paragraphs']} blank line between "
        "paragraphs, single spaces inside a line, no trailing whitespace.",
        f"EMOJI: at most {rules['emoji']['max_per_post']} from {sorted(rules['emoji']['allowed'])}, "
        f"{rules['emoji']['placement']}.",
        "BANNED: " + ", ".join(voice["banned_phrases"]) + ".",
        "BANNED CLAIMS: " + ", ".join(voice["forbidden_claims"]) + ".",
    ]
    disclaimer = DISCLAIMER_SURFACES.get(surface)
    if disclaimer:
        lines.append(f"REQUIRED FOOTER: {rules['disclaimers'][disclaimer]}")
    return "\n".join(lines)


def _citation_block(pack: dict[str, Any], style: dict[str, Any]) -> str:
    return Template(str(pack["citation_block"])).safe_substitute(
        min_sources=int(style["citations"]["min_sources"])
    )


def system_prompt(surface: str, style: dict[str, Any] | None = None) -> str:
    """System message for one surface, style contract included."""
    rules = style or load_style()
    pack = load_prompts()
    if surface not in pack["surfaces"]:
        raise KeyError(f"unknown content surface {surface!r}")
    return Template(str(pack["system"])).safe_substitute(style_block=style_block(surface, rules))


def render(
    surface: str,
    *,
    topic: str,
    evidence: str = "",
    date: str = "",
    primary_url: str = "",
    style: dict[str, Any] | None = None,
) -> str:
    """User message for one surface, with the pack's budget substituted."""
    rules = style or load_style()
    pack = load_prompts()
    if surface not in pack["surfaces"]:
        raise KeyError(f"unknown content surface {surface!r}")
    budget_key = _budget_key(pack, surface)
    values = {
        "topic": topic,
        "evidence": evidence.strip() or "(none supplied)",
        "date": date,
        "primary_url": primary_url,
        "budget": int(rules["length_budgets"][budget_key]),
        "citation_block": _citation_block(pack, rules),
        "style_block": style_block(surface, rules),
    }
    return Template(str(pack["surfaces"][surface]["template"])).safe_substitute(values).strip()
