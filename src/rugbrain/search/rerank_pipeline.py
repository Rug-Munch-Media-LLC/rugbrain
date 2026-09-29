"""Selection, noise-floor and annotation for the search-lane rerank result.

Pure post-processing over what the scoring ladder already returned: pick the
candidate ceiling by original retrieval score, drop confidently-irrelevant
chunks when there is enough signal left to honour ``keep``, and attach the
verdict metadata callers read.

The ladder itself (``_score_candidates``, the strain ledger, the health cache)
stays in :mod:`rugbrain.search.rerank` because tests mutate that state by
name; it is injected here so there is no import cycle and no second copy.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from rugbrain.search.rerank_instructions import detect_vertical
from rugbrain.search.rerank_scores import original_score

# Sigmoid P(relevant) below this is confidently-irrelevant noise (Qwen3
# rerank scores are 0-1). Applied only when enough strong candidates remain.
NOISE_FLOOR = 0.10

# (query, candidates, tier) -> (scores, method)
Scorer = Callable[[str, list[dict[str, Any]], str], Awaitable[tuple[list[float], str]]]


def select_candidates(chunks: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Top ``top_n`` chunks by original retrieval score (``<= 0`` means all).

    Cross-encoding is expensive, so only the best candidates are re-scored.
    """
    top_n = len(chunks) if top_n <= 0 else min(top_n, len(chunks))
    return sorted(chunks, key=original_score, reverse=True)[:top_n]


def tag_unreranked(candidates: list[dict[str, Any]], keep: int) -> list[dict[str, Any]]:
    """LOUD degrade path: reranker down, so tag and say so — never empty.

    Returns the best ``keep`` candidates by retrieval score with
    ``rerank_method="none"`` and ``reranked=False``, so a caller can always
    tell a degraded result from a real one. It never returns an empty list for
    a non-empty input — an empty result would read as "nothing matched".
    """
    keep = len(candidates) if keep <= 0 else min(keep, len(candidates))
    ordered = sorted(candidates, key=original_score, reverse=True)[:keep]
    for doc in ordered:
        doc["rerank_method"] = "none"
        doc["reranked"] = False
    return ordered


def apply_noise_floor(
    scored: list[tuple[dict[str, Any], float]],
    keep: int,
) -> list[tuple[dict[str, Any], float]]:
    """Drop sub-``NOISE_FLOOR`` chunks, but only when ``keep`` survive.

    Qwen3 rerank scores are sigmoid P(relevant); <0.1 is confidently
    irrelevant. Filtering is skipped when it would starve the caller, so a
    thin-signal query still gets a full result set.
    """
    strong = [(d, s) for d, s in scored if s >= NOISE_FLOOR]
    return strong if len(strong) >= keep else scored


def annotate(
    doc: dict[str, Any],
    score: float,
    method: str,
    query: str,
    duration_ms: float,
) -> dict[str, Any]:
    """Copy a chunk and attach the cross-encoder verdict."""
    entry = dict(doc)
    entry["rerank_score"] = round(float(score), 6)
    entry["rerank_method"] = method
    entry["reranked"] = True
    entry["rerank_vertical"] = detect_vertical(query)
    entry["rerank_duration_ms"] = round(duration_ms, 1)
    return entry


async def rerank_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    score_candidates: Scorer,
    top_n: int = 20,
    keep: int = 5,
    tier: str = "deep",
) -> list[dict[str, Any]]:
    """Re-rank *chunks* against *query*, keep the best.

    Parameters
    ----------
    query:
        The user query string.
    chunks:
        Candidate chunk dicts (each with ``id``/``doc_id`` and ``content`` or
        ``text``; retrieval scores under ``score``/``rrf_score``/``similarity``).
    score_candidates:
        The scoring ladder (injected to keep the strain ledger single-copy).
    top_n:
        Number of top candidates (by original retrieval score) to re-rank.
        ``<= 0`` means "score all chunks". Default 20.
    keep:
        Number of best re-ranked chunks to return. ``<= 0`` means "keep all
        scored". Default 5. Clamped to ``top_n``.

    Returns
    -------
    list[dict]
        Chunks sorted by cross-encoder relevance descending, augmented with
        ``rerank_score``, ``rerank_method``, ``reranked`` and
        ``rerank_duration_ms``. When the reranker server is unavailable, the
        top ``keep`` chunks by their original retrieval score come back with
        ``rerank_method="none"`` — search behaviour is preserved and the
        degradation is explicit, never an empty result.
    """
    if not chunks:
        return []

    candidates = select_candidates(chunks, top_n)
    keep = len(candidates) if keep <= 0 else min(keep, len(candidates))

    t0 = time.perf_counter()
    scores, method = await score_candidates(query, candidates, tier)
    duration_ms = (time.perf_counter() - t0) * 1000

    if not scores or method == "none":
        return tag_unreranked(candidates, keep)

    scored = sorted(zip(candidates, scores, strict=True), key=lambda x: x[1], reverse=True)
    scored = apply_noise_floor(scored, keep)
    return [annotate(d, s, method, query, duration_ms) for d, s in scored[:keep]]
