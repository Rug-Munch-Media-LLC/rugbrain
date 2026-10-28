"""Cross-channel duplicate collapse for the content candidate pool.

Syndication crosses SOURCES, not just channels. One wire story arrives as a
Telegram post, an X repost and an RSS item, each with its own link, its own
emoji and its own boilerplate. The previous selection keyed on a 120-character
content prefix, so the three copies had three different prefixes and all three
survived — the same story could be published twice in one cycle.

Three layers, cheapest first (:mod:`rugbrain.content_dedupe_layers` owns them
and their calibrated threshold):

    exact     normalised title+body hash — no false positives, most traffic
    near      token-shingle Jaccard — the light edits syndicators make
    semantic  corpus-lane cosine — the paraphrase no lexical layer can see

A layer only runs when the cheaper ones miss, so the semantic lane is paid for
once per genuinely hard pair, not once per pair. Embeddings are passed IN
(``vectors``, keyed by story id) and are optional: without them the first two
layers still run, and the caller can say why the third did not.

A collapsed story keeps ONE representative and remembers where else it was
seen (``also_seen_in``), because corroboration across channels is evidence the
writer may cite — it just must not become three posts. The suppressed copies
are reported (``collapse_with_report``) so the publication gate can route them
as duplicates instead of silently discarding them.
"""

from __future__ import annotations

from typing import Any

from rugbrain.content_dedupe_layers import (
    MAX_HASH_CHARS,
    NEAR_THRESHOLD,
    SEMANTIC_THRESHOLD,
    cosine,
    exact_key,
    layers_hit,
    story_text,
)
from rugbrain.ingest_sources.ingest_dedupe import DUP_THRESHOLD  # re-exported (compat)

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


def _vector(story: dict[str, Any], vectors: dict[str, list[float]] | None) -> list[float] | None:
    """The story's embedding, looked up by id and falling back to its hash."""
    if not vectors:
        return None
    return vectors.get(str(story.get("id") or "")) or vectors.get(exact_key(story))


def _merge(representative: dict[str, Any], other: dict[str, Any], layer: str) -> dict[str, Any]:
    """Record where else this story was seen, and by which layer it was caught."""
    own = str(representative.get("source") or "")
    extra = str(other.get("source") or "")
    seen = list(representative.get("also_seen_in") or [])
    if extra and extra != own and extra not in seen:
        seen.append(extra)
    representative["also_seen_in"] = seen[:MAX_ALSO_SEEN]
    representative["corroboration"] = 1 + len(representative["also_seen_in"])
    layers = set(representative.get("dedupe_layers") or [])
    layers.add(layer)
    representative["dedupe_layers"] = sorted(layers)
    return representative


def collapse_with_report(
    stories: list[dict[str, Any]],
    *,
    threshold: float = NEAR_THRESHOLD,
    vectors: dict[str, list[float]] | None = None,
    semantic_threshold: float = SEMANTIC_THRESHOLD,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collapse duplicates and report every suppressed copy.

    Returns ``(kept, suppressed)`` where each suppressed entry is
    ``{"id", "representative_id", "layer", "jaccard", "cosine"}``. Input order
    does not matter: stories are considered best-first, so the representative is
    the highest-quality copy whatever order the store returned them in.
    """
    kept: list[dict[str, Any]] = []
    by_key: dict[str, int] = {}
    suppressed: list[dict[str, Any]] = []

    for story in sorted(stories, key=_quality, reverse=True):
        text = story_text(story)
        if not text.strip():
            kept.append(story)
            continue
        key = exact_key(story)
        index = by_key.get(key)
        if index is None:
            vector = _vector(story, vectors)
            for candidate_index, other in enumerate(kept):
                if not story_text(other).strip():
                    continue
                other_vector = _vector(other, vectors)
                proof = layers_hit(
                    story,
                    other,
                    cosine_value=(
                        cosine(vector, other_vector)
                        if vector is not None and other_vector is not None
                        else None
                    ),
                    near_threshold=threshold,
                    semantic_threshold=semantic_threshold,
                )
                if proof["layer"]:
                    index = candidate_index
                    break
                story["dedupe_similarity"] = max(
                    float(story.get("dedupe_similarity") or 0.0), proof["similarity"]
                )
            if index is None:
                story.setdefault("dedupe_similarity", 0.0)
                kept.append(story)
                by_key[key] = len(kept) - 1
                continue
        else:
            proof = {"layer": "exact", "jaccard": 1.0, "cosine": None}
        representative = kept[index]
        suppressed.append(
            {
                "id": story.get("id"),
                "representative_id": representative.get("id"),
                "layer": proof.get("layer") or "exact",
                "jaccard": proof.get("jaccard"),
                "cosine": proof.get("cosine"),
            }
        )
        by_key[key] = index
        kept[index] = _merge(representative, story, str(proof.get("layer") or "exact"))
    return kept, suppressed


def collapse(
    stories: list[dict[str, Any]],
    *,
    threshold: float = NEAR_THRESHOLD,
    vectors: dict[str, list[float]] | None = None,
    semantic_threshold: float = SEMANTIC_THRESHOLD,
) -> list[dict[str, Any]]:
    """Collapse duplicates, keeping the best representative (see the report form)."""
    kept, _suppressed = collapse_with_report(
        stories, threshold=threshold, vectors=vectors, semantic_threshold=semantic_threshold
    )
    return kept


__all__ = [
    "DUP_THRESHOLD",
    "MAX_ALSO_SEEN",
    "MAX_HASH_CHARS",
    "collapse",
    "collapse_with_report",
]
