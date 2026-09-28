"""Rerank content candidates before they become a post.

`_recent_stories` fetches by vector similarity with a deliberately low floor
(`min_similarity=0.15`) so that a quiet cycle still returns something, and its
comment claimed "importance rerank does the triage" — but no rerank call ever
existed. The consequence: a dense embedding match on a shiller post outranked a
lexically-obvious exploit alert, and the channel carried whatever the vector
happened to like.

This module is the triage that comment promised. Candidates are cross-encoded
against the channel's standing editorial intent, so ordering reflects "is this
actually worth posting" rather than "does this look like the query". The
cross-encoder is the right tool here and the wrong one at ingest: scoring every
ingested message would multiply cost across every channel, while the number of
candidates that reach the content pipeline is small and bounded.

Everything degrades rather than fails. If the reranker is unavailable, the
original retrieval order is kept — a slower pipeline beats no pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The channel's editorial intent. One standing query, so a score means the same
#: thing across cycles and the ordering is comparable run to run.
EDITORIAL_INTENT = "security exploit hack rug pull scam alert important development"

#: Cross-encode only the strongest candidates; beyond this the ordering gain is
#: noise and the latency cost is not.
CANDIDATE_POOL = 30

#: How many stories a post is built from.
DEFAULT_KEEP = 8

#: Below this cross-encoder score a candidate is treated as off-topic for the
#: channel and dropped, so padding is not added to fill the quota.
MIN_RELEVANCE = 0.15


def _original_score(story: dict[str, Any]) -> float:
    """Retrieval score, used to bound the pool and as the fallback order."""
    for key in ("score", "similarity", "rrf_score", "rerank_score"):
        value = story.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


async def rerank_stories(
    stories: list[dict[str, Any]],
    *,
    intent: str = EDITORIAL_INTENT,
    keep: int = DEFAULT_KEEP,
    pool: int = CANDIDATE_POOL,
    min_relevance: float = MIN_RELEVANCE,
) -> list[dict[str, Any]]:
    """Order `stories` by editorial relevance, best first, capped at `keep`.

    Returns the input order when the reranker cannot be reached, so a reranker
    outage degrades quality rather than stopping publication.
    """
    if not stories:
        return []
    candidates = sorted(stories, key=_original_score, reverse=True)[:pool]
    try:
        from rugbrain.search.rerank import rerank_chunks

        reranked = await rerank_chunks(
            intent,
            candidates,
            top_n=pool,
            keep=pool,
            tier="fast",
        )
    except Exception as exc:
        logger.warning("content_rerank_unavailable using_retrieval_order: %s", str(exc)[:120])
        return candidates[:keep]

    scored: list[dict[str, Any]] = []
    for story in reranked:
        score = story.get("rerank_score")
        if not isinstance(score, (int, float)):
            continue
        if float(score) < min_relevance:
            continue
        scored.append(story)
    if not scored:
        logger.info(
            "content_rerank_all_below_floor count=%d floor=%.2f", len(reranked), min_relevance
        )
        return []
    return scored[:keep]
