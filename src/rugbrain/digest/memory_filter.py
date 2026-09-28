"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
The admission filter — which candidate text is worth persisting at all.

**The extraction problem.** A memory system that stores raw turns fills
up with noise. The insight: a *durable fact about a case* (this address
is a known drainer, this protocol was exploited on this date) is worth
persisting; a turn transcript is not. `should_remember` is that filter,
and it is deliberately strict — a false positive writes a memory that
will later be recalled as authoritative.

Rejection reasons are stable strings so a caller can log *why* a
candidate was dropped, which is the only way to tune this without
guessing.
"""

from __future__ import annotations

import re
from typing import Final

from rugbrain.digest.memory_types import MAX_MEMORY_CHARS
from rugbrain.digest.security import has_injection

# Facts worth carrying between sessions. Anchored so a statement about a
# *subject* is required — "X was drained by Y" is a fact; "check Y" is not.
_DURABLE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:was|were|is|has been|have been)\s+\w+ed\b",
        r"\bexploit(?:ed|ing)?\b",
        r"\bdrain(?:ed|s|ing)?\b",
        r"\brug(?:ged|pulled)?\b",
        r"\bscam(?:mer|med)?\b",
        r"\bphish(?:ing|ed)?\b",
        r"\blaunder(?:ed|ing)?\b",
        r"\bcompro mise?d\b|\bcompromised\b",
        r"\bblacklisted\b|\bwhitelisted\b",
        r"\b0x[a-fA-F0-9]{40}\b",
        r"\battacker\b|\bvictim\b|\bdeployer\b",
    )
)

# Explicitly transient: these are the shapes that make a memory system
# unusable if stored verbatim.
_TRANSIENT: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:hi|hello|hey|thanks|thank you|ok|okay|yes|no)\s*[.!?]?\s*$",
        r"\bwhat (?:is|are) your (?:name|instructions|system prompt)\b",
        r"^\s*/[a-z]+\s*$",  # slash command echo
    )
)


def should_remember(
    text: str, *, namespace: str, source: str = "agent", min_confidence: float = 0.0
) -> tuple[bool, str]:
    """Return ``(keep, reason)`` for a candidate memory."""
    stripped = text.strip()
    if not stripped:
        return False, "empty"
    if len(stripped) > MAX_MEMORY_CHARS:
        return False, "too_long"
    if any(p.search(stripped) for p in _TRANSIENT):
        return False, "transient"
    # A memory that carries an instruction is a stored prompt-injection
    # payload: it would be replayed verbatim into a future session.
    if has_injection(stripped):
        return False, "injection_shaped"
    if not any(p.search(stripped) for p in _DURABLE_PATTERNS):
        return False, "not_durable"
    if not namespace or not namespace.strip():
        return False, "no_namespace"
    _ = source, min_confidence
    return True, "ok"
