"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Style gate — the checks every rendered surface must survive.

The mechanical half of ``docs/CONTENT-STYLE.md``, all read from
``config/content_style.json``. Spacing repair lives in
:mod:`rugbrain.content_style_normalise` and is re-exported here. Lint never
rewrites meaning: it reports, and the caller blocks on what remains.
"""

from __future__ import annotations

import re
from typing import Any

from rugbrain.content_style import (
    acronym_allowlist,
    banned_patterns,
    banned_phrases,
    budget,
    load_style,
)
from rugbrain.content_style_normalise import normalise_text

__all__ = ("lint_text", "normalise_text")

_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\u2600-\u27bf\u2b00-\u2bff\u2190-\u21ff\ufe0f]"
)
_CITATION_RE = re.compile(r"\[(\d{1,2})\]")
_ALLCAPS_RE = re.compile(r"(?<![$\w])[A-Z]{4,}(?![\w])")
_APPROX_RE = re.compile(r"\b(approximately|around|roughly|about)\s+\$?\d", re.IGNORECASE)
_PERCENT_RE = re.compile(r"\b\d+\s?(percent|per cent)\b", re.IGNORECASE)
_PRICE_RE = re.compile(r"\$\d{4}(?![\d,.])")
_ORDINAL_RE = re.compile(r"\b\d{1,2}(st|nd|rd|th)\b", re.IGNORECASE)
_DOUBLE_SPACE_RE = re.compile(r"(?<=\S) {2,}(?=\S)")
_TRAILING_RE = re.compile(r"[ \t]+$", re.MULTILINE)
_FIRST_PERSON_RE = re.compile(r"(?<![\w'])I(?![\w'])")
_EXCLAIM_RE = re.compile(r"!")


def _spacing_problems(text: str) -> list[str]:
    problems: list[str] = []
    if _DOUBLE_SPACE_RE.search(text):
        problems.append("spacing: double space inside a line")
    if re.search(r"\n{3,}", text):
        problems.append("spacing: more than one blank line between paragraphs")
    if _TRAILING_RE.search(text):
        problems.append("spacing: trailing whitespace")
    if "\t" in text:
        problems.append("spacing: tab character")
    return problems


def _emoji_problems(text: str, style: dict[str, Any]) -> list[str]:
    rules = style["emoji"]
    allowed = set(rules["allowed"])
    stripped = text
    used = 0
    for emoji in allowed:
        used += stripped.count(emoji)
        stripped = stripped.replace(emoji, "")
    unknown = set(_EMOJI_RE.findall(stripped)) - {"\ufe0f"}
    problems: list[str] = []
    if unknown:
        problems.append(f"emoji: not in allowlist {sorted(unknown)}")
    if used > int(rules["max_per_post"]):
        problems.append(f"emoji: {used} used, max {rules['max_per_post']} per post")
    return problems


def _voice_problems(text: str, style: dict[str, Any]) -> list[str]:
    lowered = text.lower()
    problems = [f"voice: banned phrase {p!r}" for p in banned_phrases(style) if p in lowered]
    problems += [
        f"voice: price prediction pattern {pattern!r}"
        for pattern in banned_patterns(style)
        if re.search(pattern, text, re.IGNORECASE)
    ]
    if not style["voice"].get("first_person_singular") and _FIRST_PERSON_RE.search(text):
        problems.append("voice: first person singular")
    limit = int(style["voice"]["max_exclamation_marks"])
    if len(_EXCLAIM_RE.findall(text)) > limit:
        problems.append("voice: exclamation mark")
    return problems


def _capitalisation_problems(text: str, style: dict[str, Any]) -> list[str]:
    allowed = set(acronym_allowlist(style))
    allowed |= {str(v) for v in style["capitalization"]["severity_labels"]}
    offending = sorted({word for word in _ALLCAPS_RE.findall(text) if word not in allowed})
    return [f"capitalization: all-caps word {word!r}" for word in offending]


def _number_problems(text: str, style: dict[str, Any]) -> list[str]:
    checks = (
        (_PERCENT_RE, "numbers: percent spelled out"),
        (_PRICE_RE, "numbers: dollar amount without thousands separator"),
        (_ORDINAL_RE, "numbers: ordinal date"),
    )
    problems = [reason for pattern, reason in checks if pattern.search(text)]
    if style["numbers"].get("forbid_approximate_quantities") and _APPROX_RE.search(text):
        problems.append("numbers: approximate quantity of a verifiable figure")
    return problems


def _citation_problems(text: str, source_count: int | None) -> list[str]:
    markers = [int(m) for m in _CITATION_RE.findall(text)]
    if source_count is None:
        return []
    problems = [f"citations: marker [{m}] has no source" for m in markers if m < 1 or m > source_count]
    if source_count and not markers:
        problems.append("citations: no inline [n] marker")
    return problems


def lint_text(
    text: str,
    surface: str,
    *,
    style: dict[str, Any] | None = None,
    source_count: int | None = None,
) -> list[str]:
    """Return every style violation in ``text`` for ``surface``.

    Args:
        text: rendered copy.
        surface: key in ``length_budgets``; decides the character budget.
        style: override for tests; defaults to the canonical config.
        source_count: number of sources the copy may cite. When given, every
            ``[n]`` must fall in ``1..source_count``.

    Returns:
        A list of human-readable violations. Empty means publishable.
    """
    rules = style or load_style()
    if not text.strip():
        return ["length: empty copy"]
    problems = _spacing_problems(text)
    problems += _emoji_problems(text, rules)
    problems += _voice_problems(text, rules)
    problems += _capitalisation_problems(text, rules)
    problems += _number_problems(text, rules)
    problems += _citation_problems(text, source_count)
    if surface in rules["length_budgets"]:
        cap = budget(surface, rules)
        if len(text) > cap:
            problems.append(f"length: {len(text)} chars exceeds {surface} budget {cap}")
    return problems
