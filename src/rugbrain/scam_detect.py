"""Deterministic keyword detection over ingested text and code.

No LLM, no I/O -- just the vocabularies in :mod:`rugbrain.scam_vocab` applied to
a string. That is deliberate: these run on every ingest at zero cost, and a
heuristic that needs a model would silently stop labelling when the provider
is down.
"""

from __future__ import annotations

from rugbrain.scam_vocab import INDICATOR_KEYWORDS, VECTOR_ALIASES, VECTOR_KEYWORDS

__all__ = ["detect_attack_vectors", "detect_indicators", "normalize_vector"]


def normalize_vector(value: str | None) -> str:
    """Canonical attack-vector label (lowercase, hyphenated, default unknown)."""
    if not value:
        return "unknown"
    v = value.strip().lower().replace("_", "-").replace(" ", "-")
    # Map category names to canonical vectors when the vector is a category.
    return VECTOR_ALIASES.get(v, v)


def detect_attack_vectors(text: str) -> list[str]:
    """Deterministically detect attack vectors mentioned in *text*.

    Returns canonical vector labels ordered by keyword-match confidence
    (most keyword hits first). Falls back to ``["unknown"]`` when nothing
    matches.
    """
    low = text.lower() if text else ""
    hits: list[tuple[int, str]] = []
    for vector, keywords in VECTOR_KEYWORDS:
        count = sum(1 for kw in keywords if kw in low)
        if count > 0:
            hits.append((count, vector))
    # Sort by keyword-hit count descending; stable sort keeps vocabulary
    # order for ties (more specific vectors are listed earlier).
    hits.sort(key=lambda pair: pair[0], reverse=True)
    return [v for _, v in hits] or ["unknown"]


def detect_indicators(code: str) -> list[str]:
    """Return indicator labels present in *code* (deterministic keyword scan)."""
    if not code:
        return []
    low = code.lower()
    found: list[str] = []
    for label, keywords in INDICATOR_KEYWORDS:
        if any(kw in low for kw in keywords):
            found.append(label)
    return found
