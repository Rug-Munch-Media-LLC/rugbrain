"""Candidate selection for the content pipeline.

Split out of :mod:`rugbrain.content_engine` so that module stays inside the
200-line limit, and because "which stories are worth posting" is a decision
worth owning on its own.

Two stages, deliberately:

1. **Retrieve** — one vector search per dated collection, with a deliberately
   low similarity floor so a quiet cycle still returns something.
2. **Rerank** — the cross-encoder orders the candidates by the channel's
   editorial intent, so the order reflects "is this worth posting" rather than
   "does this look like the query".

Only dated collections are probed. `threat_intel_v2` is an undated historical
knowledge base; including it re-introduces the stale-news incident of 2026-08-26.
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain import content_rerank
from rugbrain import content_story as story_shape

logger = logging.getLogger(__name__)

#: Collections eligible for channel content, and nothing else.
CANDIDATE_COLLECTIONS = ("news_v2",)

#: The interim embedder yields lower cosine scores than qwen, so the floor is
#: low and the cross-encoder does the real triage.
MIN_SIMILARITY = 0.15

#: Over-fetch before dedupe, so a burst of near-identical posts does not starve
#: the post of distinct stories.
FETCH_MULTIPLIER = 2


async def recent_stories(limit: int = 60) -> list[dict[str, Any]]:
    """Best `limit` stories for the next post, deduped and reranked."""
    from rugbrain.embeddings import get_query_embedding
    from rugbrain.qdrant_store import get_qdrant_store

    store = await get_qdrant_store()
    embedding = await get_query_embedding(content_rerank.EDITORIAL_INTENT)
    seen: dict[str, dict[str, Any]] = {}
    for collection in CANDIDATE_COLLECTIONS:
        try:
            results = await store.search(
                embedding,
                collection=collection,
                limit=limit * FETCH_MULTIPLIER,
                min_similarity=MIN_SIMILARITY,
            )
        except Exception as exc:
            logger.debug("content_probe_failed collection=%s err=%s", collection, str(exc)[:80])
            continue
        for result in results:
            key = story_shape.dedupe_key(result)
            if not key or key in seen:
                continue
            seen[key] = story_shape.normalise(result, collection)
    ranked = story_shape.rank(seen)
    return await content_rerank.rerank_stories(ranked, keep=limit)
