"""Boundary-aware chunk spans: paragraph → line → sentence → space.

Boundaries are chosen inside a ``size`` window and never fall mid-word. If the
window contains no boundary, the span extends up to ``max_chars`` looking for a
space (the hard-cap fallback). Only a text with no whitespace at all inside the
cap window — an indivisible token — is cut at the cap, because no word boundary
exists there; this is the single documented exception to the no-mid-word rule.

Overlap keeps the tail of the previous span at the head of the next, snapped
forward to a space so the overlap never starts mid-word either.
"""

from __future__ import annotations

import re

_SENTENCE = re.compile(r"[.!?\u2026][\"'\u201d\u2019)\]]?\s")
_LATTER_HALF = 0.5
DEFAULT_MIN_CHARS = 20


def chunk_spans(
    text: str,
    size: int,
    overlap: float | int = 0.0,
    max_chars: int | None = None,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> list[tuple[int, int]]:
    """Return ``(start, end)`` spans of at most *size* chars (hard cap *max_chars*).

    ``overlap`` is a ratio in ``[0, 1)`` of *size*, or an absolute char count when
    ``>= 1``. Consecutive spans overlap by that amount, snapped to a word start.
    """
    n = len(text)
    if n == 0 or size <= 0:
        return []
    if n <= size:
        return [(0, n)]
    cap = max(size, max_chars or size)
    ov = int(size * overlap) if 0 <= overlap < 1 else int(overlap)
    floor = int(size * _LATTER_HALF)
    spans: list[tuple[int, int]] = []
    start = 0
    while start < n:
        while start < n and text[start].isspace():
            start += 1
        if start >= n:
            break
        if start + size >= n:
            spans.append((start, n))
            break
        window = text[start : start + size]
        end = -1
        for sep in ("\n\n", "\n"):
            pos = window.rfind(sep)
            if pos >= floor:
                end = start + pos + len(sep)
                break
        if end < 0:
            ends = [m.end() for m in _SENTENCE.finditer(window) if m.end() >= floor]
            if ends:
                end = start + ends[-1]
        if end < 0:
            pos = window.rfind(" ")
            if pos >= floor:
                end = start + pos + 1
        if end < 0:
            pos = text.rfind(" ", start + floor, min(start + cap, n))
            # No whitespace anywhere in the cap window: an indivisible token.
            end = pos + 1 if pos != -1 else min(start + cap, n)
        spans.append((start, end))
        nxt = max(end - ov, start + 1) if ov > 0 else end
        if nxt < n and nxt > 0 and text[nxt - 1].isalnum() and text[nxt].isalnum():
            space = text.find(" ", nxt)
            if space != -1:
                nxt = space + 1
        start = nxt
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and len(text[s:e].strip()) < min_chars:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [(s, e) for s, e in merged if text[s:e].strip()]
