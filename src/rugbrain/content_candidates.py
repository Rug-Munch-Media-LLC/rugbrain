"""Candidate selection for the content pipeline.

Split out of :mod:`rugbrain.content_engine` so that module stays inside the
200-line limit, and because "which stories are worth posting" is a decision
worth owning on its own.

Three stages, deliberately:

1. **Retrieve** — one vector search per dated collection, with a deliberately
   low similarity floor so a quiet cycle still returns something.
2. **Retrieve the X lane** — the same search filtered to ``x_social``. X posts
   are a small family inside a 298k-point collection, so an unfiltered vector
   search never admitted one: measured 2026-10-24, top 120 hits for the editorial
   intent contained 0 X posts while 1,821 sat in the same collection. Scraped X
   posts must reach the cross-encoder, so this lane is probed explicitly.
3. **Rerank** — the cross-encoder orders the merged pool by the channel's
   editorial intent, so the order reflects "is this worth posting" rather than
   "does this look like the query".

Only dated collections are probed. `threat_intel_v2` is an undated historical
knowledge base; including it re-introduces the stale-news incident of 2026-08-26.

Nothing here raises: an unreachable embedder or store degrades to fewer (or
zero) candidates and records WHY in ``LAST_REASON``, so a quiet cycle is never
mistaken for an empty corpus.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from rugbrain import content_rerank
from rugbrain import content_story as story_shape

logger = logging.getLogger(__name__)

#: DI seams: the embedder and the store are passed IN, so a test (or a caller
#: with its own pool) never has to patch a module attribute.
EmbedFn = Callable[[str], Awaitable[list[float]]]
StoreFn = Callable[[], Awaitable[Any]]
#: The triage stage: (stories, keep=) → ordered stories. Injectable for tests.
RerankFn = Callable[..., Awaitable[list[dict[str, Any]]]]

#: Collections eligible for channel content, and nothing else.
CANDIDATE_COLLECTIONS = ("news_v2",)

#: The interim embedder yields lower cosine scores than qwen, so the floor is
#: low and the cross-encoder does the real triage.
MIN_SIMILARITY = 0.15

#: Over-fetch before dedupe, so a burst of near-identical posts does not starve
#: the post of distinct stories.
FETCH_MULTIPLIER = 2

#: X posts are stored under ``metadata.category = x_social`` (see
#: ``ingest_sources.x_intel.storage.DOC_CATEGORY``). Filtered, not ranked out.
X_CATEGORY = "x_social"

#: How many X candidates a cycle may contribute to the pool.
X_FETCH = 20

#: Why the last selection returned what it did ("" = no degradation observed).
LAST_REASON: str = ""


def _note(reason: str) -> None:
    """Record the reason a stage degraded, without raising."""
    global LAST_REASON
    LAST_REASON = reason
    logger.warning("content_candidates_degraded reason=%s", reason)


async def _default_embed(text: str) -> list[float]:
    """The shared query embedder, imported late so tests can pass their own."""
    from rugbrain.embeddings import get_query_embedding

    return await get_query_embedding(text)


async def _default_store() -> Any:
    """The shared Qdrant store, imported late so tests can pass their own."""
    from rugbrain.qdrant_store import get_qdrant_store

    return await get_qdrant_store()


async def _probe(
    store: Any,
    embedding: list[float],
    collection: str,
    limit: int,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """One vector search, fail-open to ``[]`` with the failure recorded."""
    try:
        return await store.search(
            embedding,
            collection=collection,
            limit=limit,
            min_similarity=MIN_SIMILARITY,
            filters=filters,
        )
    except Exception as exc:
        _note(f"search_failed:{collection}:{str(exc)[:80]}")
        return []


async def recent_stories(
    limit: int = 60,
    *,
    embed: EmbedFn | None = None,
    store_fn: StoreFn | None = None,
    rerank: RerankFn | None = None,
) -> list[dict[str, Any]]:
    """Best `limit` stories for the next post, deduped and reranked.

    *embed* / *store_fn* / *rerank* are the DI seams (defaults: the shared
    embedder, store and cross-encoder). Degradation is reported through
    ``LAST_REASON``, never raised.
    """
    global LAST_REASON
    LAST_REASON = ""
    embedder: EmbedFn = embed or _default_embed
    get_store: StoreFn = store_fn or _default_store
    try:
        embedding = await embedder(content_rerank.EDITORIAL_INTENT)
    except Exception as exc:
        _note(f"embed_unavailable:{str(exc)[:120]}")
        return []
    try:
        store = await get_store()
    except Exception as exc:
        _note(f"store_unavailable:{str(exc)[:120]}")
        return []

    seen: dict[str, dict[str, Any]] = {}
    for collection in CANDIDATE_COLLECTIONS:
        results = await _probe(store, embedding, collection, limit * FETCH_MULTIPLIER)
        for result in results:
            key = story_shape.dedupe_key(result)
            if not key or key in seen:
                continue
            seen[key] = story_shape.normalise(result, collection)
        # Same collection, different family: the X lane is always probed, so a
        # scraped post is a candidate on merit rather than on luck.
        for result in await _probe(
            store, embedding, collection, X_FETCH, filters={"metadata.category": X_CATEGORY}
        ):
            key = story_shape.dedupe_key(result)
            if not key or key in seen:
                continue
            seen[key] = story_shape.normalise(result, collection)
    ranked = story_shape.rank(seen)
    triage: RerankFn = rerank or content_rerank.rerank_stories
    return await triage(ranked, keep=limit)


__all__ = ["CANDIDATE_COLLECTIONS", "LAST_REASON", "MIN_SIMILARITY", "X_CATEGORY", "recent_stories"]
