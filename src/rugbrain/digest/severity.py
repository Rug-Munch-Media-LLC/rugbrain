"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Severity classification — the signal that decides where a document lands.

One job: map text to ``(severity, matched_rule_phrases)``. Nothing here
knows about addresses, graphs or Qdrant.

The lexicon itself lives in :mod:`rugbrain.digest.lexicons`; this module
is the first-match-wins rule over it. The ordering is the point: the most
specific severity wins, so a document that trips both "critical" and
"high" is classified critical.
"""

from __future__ import annotations

from rugbrain.digest.lexicons import SEVERITY_RULES


def classify_severity(text: str) -> tuple[str, tuple[str, ...]]:
    """Return ``(severity, matched_rule_phrases)`` for *text*.

    Returns ``("low", ())`` when nothing matches — an unmatched document
    is not a zero-severity document, it is an unclassified one, and the
    caller decides what to do with that.
    """
    lowered = text.lower()
    for level, phrases in SEVERITY_RULES:
        hits = [p for p in phrases if p in lowered]
        if hits:
            return level, tuple(hits)
    return "low", ()
