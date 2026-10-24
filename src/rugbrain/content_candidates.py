"""Candidate selection for the content pipeline.

Split out of :mod:`rugbrain.content_engine` so that module stays inside the
200-line limit, and because "which stories are worth posting" is a decision
worth owning on its own.

Four stages, one pool:

1. **Retrieve** — one vector search per source-family collection at a
   deliberately low similarity floor, so a quiet cycle still returns something.
   Every writer feeds the SAME pool: no per-channel special case, so a
   Telegram post, an X post and an RSS research item compete on one ranking.
2. **Retrieve the X lane** — the same search filtered to ``x_social`` inside
   ``news_v2``. X posts are a small family inside a 299k-point collection, so
   an unfiltered search never admitted one: measured 2026-10-24, the top 120
   hits held 0 X posts while 1,821 sat in the same collection.
3. **Collapse cross-channel duplicates** — one story syndicated to a Telegram
   channel, an X account and a feed is ONE story (see
   :mod:`rugbrain.content_dedupe`). Each copy used to carry its own content
   prefix, so all of them could post.
4. **Rerank** — the cross-encoder orders the merged pool by editorial intent,
   so the order reflects "is this worth posting", not "does this look like the
   query".

Only dated collections are probed (``threat_intel_v2`` is an undated knowledge
base and re-introduces the stale-news incident of 2026-08-26). Stories whose
timestamp will not normalise are dropped here, not at the publisher, so an
unpublishable story never occupies a rerank slot.

Nothing here raises: an unreachable embedder or store degrades to fewer (or
zero) candidates and records WHY in ``LAST_REASON``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from rugbrain import content_dedupe, content_rerank
from rugbrain import content_story as story_shape

logger = logging.getLogger(__name__)

#: DI seams: the embedder and the store are passed IN, so a test (or a caller
#: with its own pool) never has to patch a module attribute.
EmbedFn = Callable[[str], Awaitable[list[float]]]
StoreFn = Callable[[], Awaitable[Any]]
#: The triage stage: (stories, keep=) → ordered stories. Injectable for tests.
RerankFn = Callable[..., Awaitable[list[dict[str, Any]]]]

#: Collections eligible for channel content, one per SOURCE FAMILY.
#:
#:   news_v2       Telegram web/preview sweep + the X lane (``x_social``)
#:   social_intel  Telegram userbot / alt ingest (social chatter)
#:   research_v2   RSS/Atom research feeds (``ingest_sources.feeds``)
#:   news_articles Firehose/backend news feeds (``firehose.direct_sources``)
#:
#: EXCLUDED on purpose, each measured:
#:   threat_intel_v2, scam_intel_v2, rag_documents, known_scams — undated
#:   reference corpora (blacklists, phishing feeds, historical incidents).
#:   Nothing in them is a story, and probing them re-publishes old news.
CANDIDATE_COLLECTIONS = ("news_v2", "social_intel", "research_v2", "news_articles")

#: Collections whose payload may carry the X lane. The filter is scoped, not
#: sprayed across every collection — it is a partition of one corpus.
X_LANE_COLLECTIONS = ("news_v2",)

#: The interim embedder yields lower cosine scores than qwen, so the floor is
#: low and the cross-encoder does the real triage.
MIN_SIMILARITY = 0.15

#: X posts are stored under ``metadata.category = x_social`` (see
#: ``ingest_sources.x_intel.storage.DOC_CATEGORY``). Filtered, not ranked out.
X_CATEGORY = "x_social"

#: How many X candidates a cycle may contribute to the pool.
X_FETCH = 20

#: Bound on the pool handed to the O(n²) near-duplicate collapse. Four
#: collections over-fetch; the importance pre-sort keeps the best 200.
DEDUPE_POOL = 200

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


async def _collect(store: Any, embedding: list[float], limit: int) -> dict[str, dict[str, Any]]:
    """Every source family's hits, keyed by cheap identity, first copy wins."""
    seen: dict[str, dict[str, Any]] = {}
    for collection in CANDIDATE_COLLECTIONS:
        results = await _probe(store, embedding, collection, limit)
        # Same collection, different family: the X lane is always probed, so a
        # scraped post is a candidate on merit rather than on luck.
        if collection in X_LANE_COLLECTIONS:
            results += await _probe(
                store, embedding, collection, X_FETCH, filters={"metadata.category": X_CATEGORY}
            )
        for result in results:
            key = story_shape.dedupe_key(result)
            if not key or key in seen:
                continue
            seen[key] = story_shape.normalise(result, collection)
    return seen


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

    seen = await _collect(store, embedding, limit)
    pool = story_shape.rank(seen)[:DEDUPE_POOL]
    collapsed = content_dedupe.collapse(pool)
    dated = [story for story in collapsed if story["ts"]]
    if len(dated) < len(collapsed):
        logger.info("content_candidates_undated_dropped n=%d", len(collapsed) - len(dated))
    if not dated:
        _note(f"empty_pool:{','.join(CANDIDATE_COLLECTIONS)}")
        return []
    triage: RerankFn = rerank or content_rerank.rerank_stories
    return await triage(dated, keep=limit)


__all__ = [
    "CANDIDATE_COLLECTIONS",
    "LAST_REASON",
    "MIN_SIMILARITY",
    "X_CATEGORY",
    "X_LANE_COLLECTIONS",
    "recent_stories",
]
