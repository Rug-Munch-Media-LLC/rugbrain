"""Cross-channel duplicate collapse for the content candidate pool.

Syndication crosses SOURCES, not just channels. One wire story arrives as a
Telegram post, an X repost and an RSS item, each with its own link, its own
emoji and its own boilerplate. The previous selection keyed on a 120-character
content prefix, so the three copies had three different prefixes and all three
survived — the same story could be published twice in one cycle.

The collapse reuses the ingest-side near-duplicate detector
(:mod:`rugbrain.ingest_sources.ingest_dedupe`) instead of inventing a second
similarity measure: exact normalised-text hash first (cheap, no false
positives), then token-shingle Jaccard for the light edits syndicators make.

A collapsed story keeps ONE representative and remembers where else it was
seen (``also_seen_in``), because corroboration across channels is evidence the
writer may cite — it just must not become three posts.
"""

from __future__ import annotations

from typing import Any

from rugbrain.ingest_sources.ingest_dedupe import (
    DUP_THRESHOLD,
    jaccard,
    normalize_for_dedupe,
    shingles,
)

#: How many alternative sources a representative records. Corroboration is
#: evidence, not a leaderboard; beyond a handful the field is just noise.
MAX_ALSO_SEEN = 6

_SEVERITY_ORDER = {"critical": 3, "high": 2, "medium": 1, "low": 0}


def _quality(story: dict[str, Any]) -> tuple[float, int, int]:
    """Sort key: importance, then severity, then richer body."""
    return (
        float(story.get("importance") or 0.0),
        _SEVERITY_ORDER.get(str(story.get("severity") or "").lower(), 1),
        len(str(story.get("content") or "")),
    )


def _merge(representative: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """Record where else this story was seen, on the representative."""
    own = str(representative.get("source") or "")
    extra = str(other.get("source") or "")
    seen = list(representative.get("also_seen_in") or [])
    if extra and extra != own and extra not in seen:
        seen.append(extra)
    representative["also_seen_in"] = seen[:MAX_ALSO_SEEN]
    representative["corroboration"] = 1 + len(representative["also_seen_in"])
    return representative


def collapse(
    stories: list[dict[str, Any]],
    *,
    threshold: float = DUP_THRESHOLD,
) -> list[dict[str, Any]]:
    """Collapse cross-channel duplicates, keeping the best representative.

    Input order does not matter: stories are considered best-first, so the
    representative is the highest-importance copy whatever order the store
    returned them in.
    """
    kept: list[dict[str, Any]] = []
    fingerprints: list[set[tuple[str, ...]]] = []
    by_text: dict[str, int] = {}

    for story in sorted(stories, key=_quality, reverse=True):
        text = normalize_for_dedupe(str(story.get("content") or story.get("title") or ""))
        if not text:
            kept.append(story)
            continue
        index = by_text.get(text)
        if index is None:
            fingerprint = shingles(text)
            index = next(
                (
                    i
                    for i, other in enumerate(fingerprints)
                    if jaccard(fingerprint, other) >= threshold
                ),
                None,
            )
            if index is None:
                kept.append(story)
                fingerprints.append(fingerprint)
                by_text[text] = len(kept) - 1
                continue
            by_text[text] = index
        kept[index] = _merge(kept[index], story)
    return kept


__all__ = ["MAX_ALSO_SEEN", "collapse"]
