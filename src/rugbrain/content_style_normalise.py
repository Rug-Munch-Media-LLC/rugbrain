"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Mechanical spacing repair: the only style fix applied without human review.

``normalise_text`` never changes wording, punctuation or citations — it removes
trailing spaces, collapses double spaces and reduces blank-line runs to one.
Everything else is reported by :mod:`rugbrain.content_style_lint` and fixed by
an editor.
"""

from __future__ import annotations

import re

_TRAILING_RE = re.compile(r"[ \t]+$", re.MULTILINE)
_DOUBLE_SPACE_RE = re.compile(r"(?<=\S) {2,}(?=\S)")


def normalise_text(text: str) -> str:
    """Repair spacing only: trailing spaces, double spaces, blank-line runs."""
    lines = [_TRAILING_RE.sub("", line) for line in text.replace("\t", " ").split("\n")]
    joined = "\n".join(lines)
    joined = _DOUBLE_SPACE_RE.sub(" ", joined)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()
