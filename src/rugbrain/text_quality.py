"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Content-quality predicate for document ingestion.

A page that rendered only its JavaScript bootstrap is not documentation. Storing
it is worse than storing nothing: the collection count says "covered" while the
retrieval surface is noise. This module is the single gate every chunk passes
before an upsert, so the decision is auditable and identical for every source.

Three independent failures, each with an explicit threshold:

* shell markup — more than ``MAX_SCRIPT_RATIO`` of the characters sit inside
  ``<script>``/``<style>`` (the page was never rendered);
* runtime markers — ``window.__*``, GTM, Cloudflare challenge, "enable
  JavaScript" (the text is a bootstrapping instruction, not prose);
* too little prose — fewer than ``MIN_PROSE_WORDS`` prose words, or more than
  ``MAX_CODE_RATIO`` of the lines are code (a JS bundle dumped as text).

Anything rejected is *recorded* with its reason; nothing is silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: Characters inside <script>/<style> above this share -> shell, not a page.
MAX_SCRIPT_RATIO: Final = 0.30
#: Below this many prose words the chunk cannot answer a question.
MIN_PROSE_WORDS: Final = 40
#: Share of code-shaped lines above which a "chunk" is a dumped bundle.
MAX_CODE_RATIO: Final = 0.30

#: Substrings that only ever appear in bootstrapping/shell markup.
FORBIDDEN_MARKERS: Final = (
    "window.__",
    "googletagmanager",
    "reactroutercontext",
    "__next_data__",
    "cf-challenge",
    "cf_chl",
    "enable javascript",
    "just a moment",
    "verify you are human",
    "webpackjsonp",
)

_SCRIPT_BLOCK = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_WORD = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")
_CODE_LINE = re.compile(r"^\s*(//|/\*|\*|import\s|export\s|const\s|let\s|var\s|function\s|return\s|[})];?\s*$)")
_BRACE = re.compile(r"[{};]")


@dataclass(frozen=True)
class Verdict:
    """One chunk's fitness verdict. ``reason`` is ``ok`` when it passes."""

    ok: bool
    reason: str
    words: int
    chars: int
    script_ratio: float
    code_ratio: float

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "words": self.words,
            "chars": self.chars,
            "script_ratio": round(self.script_ratio, 3),
            "code_ratio": round(self.code_ratio, 3),
        }


def _code_ratio(text: str) -> float:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    code = sum(1 for ln in lines if _CODE_LINE.match(ln) or len(_BRACE.findall(ln)) >= 3)
    return code / len(lines)


def judge_text(text: str) -> Verdict:
    """Decide whether ``text`` may be written to a retrieval collection."""
    body = text or ""
    chars = len(body)
    if not body.strip():
        return Verdict(False, "blocked.empty", 0, chars, 0.0, 0.0)

    script_chars = sum(len(m.group(0)) for m in _SCRIPT_BLOCK.finditer(body))
    script_ratio = script_chars / chars if chars else 0.0
    lowered = body.lower()
    marker = next((m for m in FORBIDDEN_MARKERS if m in lowered), "")
    prose = _SCRIPT_BLOCK.sub(" ", body)
    words = len(_WORD.findall(prose))
    code_ratio = _code_ratio(prose)

    if script_ratio > MAX_SCRIPT_RATIO:
        return Verdict(False, "blocked.render:script_ratio", words, chars, script_ratio, code_ratio)
    if marker:
        return Verdict(False, f"blocked.render:marker:{marker}", words, chars, script_ratio, code_ratio)
    if words < MIN_PROSE_WORDS:
        return Verdict(False, "blocked.thin:words", words, chars, script_ratio, code_ratio)
    if code_ratio > MAX_CODE_RATIO:
        return Verdict(False, "blocked.render:code_ratio", words, chars, script_ratio, code_ratio)
    return Verdict(True, "ok", words, chars, script_ratio, code_ratio)


def accepted(text: str) -> bool:
    """Convenience for call sites that only need the boolean."""
    return judge_text(text).ok
