"""Tier-labeled retrieval over the ONE canonical embedder (2026-09-28).

ROOT CAUSE FIXED HERE: this module used to own a private HTTP embed client
pointing at ``QWEN_EMBED_URL`` (local :8004). That lane is MASKED and dead,
so every query produced ``both tiers unavailable`` and ZERO documents while
the rest of the fleet — which embeds through
:func:`rugbrain.embeddings.get_query_embedding` — worked fine. Two code
paths disagreed about which embedder was live, and the disagreement was
invisible because the failure degraded to an empty result set.

There is now ONE embedder seam (:func:`rugbrain.search.tiers.embed_query`)
and it delegates to ``get_query_embedding``; nothing here talks HTTP to an
embedder. The tier labels are kept for API compatibility: the 0.6B "speed"
tier was retired (2026-08-29) and its endpoints removed (2026-09-14), so both
labels resolve to the same canonical qwen3-embedding-4b lane.

Failures are EXPLICIT, never silent:

* :class:`EmbeddingDimMismatch` / :class:`UnknownCollection` propagate —
  a wrong-dimension query raises instead of returning ``[]``.
* When the embedder is genuinely unreachable, :meth:`DualTierRetriever.retrieve`
  returns ``tier="unavailable"`` with a populated ``reason``.
  ``tier="unavailable"`` is distinguishable from ``tier="ok"`` with zero
  hits, so "the embedder is down" can never read as "nothing matched".

Rerank is unchanged in spirit: candidates go through
:func:`rugbrain.search.rerank.rerank_chunks`, which keeps its documented
fail-open (reranker down -> original order, tagged ``rerank_method="none"``).

The machinery lives in :mod:`rugbrain.search.retriever`; the tier vocabulary
and the embedder seam live in :mod:`rugbrain.search.tiers`. This module stays
the import path every existing caller already uses.
"""

from __future__ import annotations

from rugbrain.search.dim_guard import EmbeddingDimMismatch
from rugbrain.search.retriever import (
    COLLECTION,
    DEEP_RECALL,
    QDRANT_URL,
    ROUTING_THRESHOLD_WORDS,
    SPEED_RECALL,
    TIER_PLAN,
    TOP_CONTEXT,
    DualTierRetriever,
    pick_tier,
)
from rugbrain.search.tiers import (
    TIER_OK,
    TIER_UNAVAILABLE,
    RetrievalResult,
    embed_query,
)

__all__ = [
    "COLLECTION",
    "DEEP_RECALL",
    "QDRANT_URL",
    "ROUTING_THRESHOLD_WORDS",
    "SPEED_RECALL",
    "TIER_OK",
    "TIER_PLAN",
    "TIER_UNAVAILABLE",
    "TOP_CONTEXT",
    "DualTierRetriever",
    "EmbeddingDimMismatch",
    "RetrievalResult",
    "embed_query",
    "pick_tier",
]
