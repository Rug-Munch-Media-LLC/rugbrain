"""Extractive unit selection: extract, slice, truncate, select, compress.

Moved verbatim from ``prompt_compression.py``. Depends only on
:mod:`.signals` for the scoring primitives, so it is pure and testable.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from rugbrain.prompt_compression_pkg.signals import (
    _ADDRESS_RE,
    _CHARS_PER_TOKEN,
    _CODE_BLOCK_SCORE,
    _FENCED_CODE_RE,
    _HAS_NUMBER_RE,
    _PromptUnit,
    _protect_spans,
    _score_unit,
    _split_sentences,
    _unprotect,
)


def _extract_units(
    text: str,
    *,
    keep_code_blocks: bool = True,
    keep_addresses: bool = True,
) -> list[_PromptUnit]:
    """Split *text* into scored units (sentences + atomic code blocks)."""
    units: list[_PromptUnit] = []
    rest = text

    if keep_code_blocks:
        # Fenced code blocks come out first and stay atomic (highest priority).

        def _code_cb(m: re.Match[str]) -> str:
            units.append(
                _PromptUnit(
                    text=m.group(0),
                    score=_CODE_BLOCK_SCORE,
                    order=len(units),
                    is_code=True,
                )
            )
            return ""

        rest = _FENCED_CODE_RE.sub(_code_cb, rest)

    protected, addresses, code_spans = _protect_spans(rest, protect_addresses=keep_addresses)
    for sentence in _split_sentences(protected):
        real = _unprotect(sentence, addresses, code_spans)
        if not real.strip():
            continue
        units.append(
            _PromptUnit(
                text=real,
                score=_score_unit(
                    real,
                    has_addr="\x00A" in sentence,
                    has_code_span="\x00C" in sentence,
                ),
                order=len(units),
            )
        )
    return units


def _slice_sentence(
    sentence: str,
    budget_tokens: int,
    count_tokens: Callable[[str], int],
) -> str:
    """Slice an oversized sentence around its high-signal tokens (address intact)."""
    limit = max(4, budget_tokens * _CHARS_PER_TOKEN)
    if count_tokens(sentence) <= budget_tokens:
        return sentence

    matches = list(_ADDRESS_RE.finditer(sentence))
    anchor = matches[0].start() if matches else None
    if anchor is None:
        number_m = _HAS_NUMBER_RE.search(sentence)
        anchor = number_m.start() if number_m else None

    if anchor is not None:
        half = limit // 2
        start = max(0, anchor - half)
        end = min(len(sentence), anchor + half)
        # Expand so every address touching the window survives whole.
        for m in matches:
            if m.start() < end and m.end() > start:
                start = min(start, m.start())
                end = max(end, m.end())
        if start > 0:
            space = sentence.rfind(" ", 0, start)
            if space != -1:
                start = space + 1
        if end < len(sentence):
            space = sentence.find(" ", end)
            if space != -1:
                end = space
        return sentence[start:end]

    head = sentence[:limit]
    return head.rsplit(" ", 1)[0] if " " in head else head


def _truncate_unit(
    unit: _PromptUnit,
    budget_tokens: int,
    count_tokens: Callable[[str], int],
) -> _PromptUnit:
    """Truncate an over-budget unit at a safe boundary (line / sentence)."""
    if unit.is_code:
        keep: list[str] = []
        used = 0
        for line in unit.text.splitlines():
            toks = count_tokens(line)
            if keep and used + toks > budget_tokens:
                break
            keep.append(line)
            used += toks
        # A single oversized line is kept whole — splitting code mid-line would
        # corrupt it; code integrity matters more than the token budget.
        return _PromptUnit("\n".join(keep) if keep else unit.text, unit.score, unit.order, True)

    keep = []
    used = 0
    for frag in _split_sentences(unit.text):
        toks = count_tokens(frag)
        if not keep and toks > budget_tokens:
            # Oversized single sentence: slice around its high-signal tokens.
            return _PromptUnit(
                _slice_sentence(frag, budget_tokens, count_tokens),
                unit.score,
                unit.order,
            )
        if keep and used + toks > budget_tokens:
            break
        keep.append(frag)
        used += toks
    return _PromptUnit(" ".join(keep), unit.score, unit.order)


def _select_units(
    units: list[_PromptUnit],
    budget_tokens: int,
    count_tokens: Callable[[str], int],
) -> list[_PromptUnit]:
    """Pick the best units fitting the budget; the top unit always survives.

    The single best unit is always included (truncated when oversized) so
    compression never keeps filler over high-signal content; the remaining
    budget is filled with the next-best fitting units. Result keeps original
    document order.
    """
    if not units:
        return []

    ordered = sorted(units, key=lambda u: (-u.score, u.order))
    chosen: list[_PromptUnit] = []
    best = ordered[0]
    best_toks = count_tokens(best.text)
    if best_toks <= budget_tokens:
        chosen.append(best)
        remaining = budget_tokens - best_toks
    else:
        chosen.append(_truncate_unit(best, budget_tokens, count_tokens))
        remaining = budget_tokens - count_tokens(chosen[0].text)

    for unit in ordered[1:]:
        toks = count_tokens(unit.text)
        if toks <= remaining:
            chosen.append(unit)
            remaining -= toks

    chosen.sort(key=lambda u: u.order)
    return chosen


def _truncate_text_head(
    text: str,
    budget_tokens: int,
    count_tokens: Callable[[str], int],
) -> str:
    """Last-resort fallback: keep the leading sentences up to the budget."""
    keep: list[str] = []
    used = 0
    for frag in _split_sentences(text):
        toks = count_tokens(frag)
        if keep and used + toks > budget_tokens:
            break
        keep.append(frag)
        used += toks
    return " ".join(keep) if keep else text[: max(4, budget_tokens * _CHARS_PER_TOKEN)]


def _compress_text(
    text: str,
    budget_tokens: int,
    *,
    count_tokens: Callable[[str], int],
    keep_code_blocks: bool = True,
    keep_addresses: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Extractive compression of a single text; returns ``(text, stats)``."""
    text = (text or "").replace("\x00", "")
    stats: dict[str, Any] = {"kept_code_blocks": 0, "kept_addresses": 0}
    if budget_tokens <= 0 or count_tokens(text) <= budget_tokens:
        return text, stats

    units = _extract_units(
        text,
        keep_code_blocks=keep_code_blocks,
        keep_addresses=keep_addresses,
    )
    selected = _select_units(units, budget_tokens, count_tokens)
    if not selected:
        return _truncate_text_head(text, budget_tokens, count_tokens), stats

    out = "\n\n".join(u.text for u in selected)
    stats["kept_code_blocks"] = sum(1 for u in selected if u.is_code)
    stats["kept_addresses"] = len(_ADDRESS_RE.findall(out))
    return out, stats


def _extractive_compress(
    text: str,
    budget: int,
    count_tokens: Callable[[str], int],
    keep_code_blocks: bool,
    keep_addresses: bool,
) -> tuple[str, str, dict[str, Any]]:
    """Run the extractive compressor; returns ``(text, method, stats)``."""
    out, stats = _compress_text(
        text,
        budget,
        count_tokens=count_tokens,
        keep_code_blocks=keep_code_blocks,
        keep_addresses=keep_addresses,
    )
    return out, "extractive", stats


# ── "lost in the middle" inversion ────────────────────────────────────────
