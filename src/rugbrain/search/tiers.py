"""Tier vocabulary for retrieval — the result record and the embedder seam.

Extracted from :mod:`rugbrain.search.dual_tier` (2026-09-29) so the two
contracts that must never be violated live in one readable place:

1. :class:`RetrievalResult` distinguishes ``tier="unavailable"`` (the
   embedder could not be reached — infrastructure down) from
   ``tier in ("speed", "deep")`` with empty ``contexts`` (a real search
   ran and matched nothing). Collapsing the two is how a dead lane scored
   0.000 while looking healthy.

2. :func:`embed_query` is THE ONE embedder seam for search. It delegates to
   :func:`rugbrain.embeddings.get_query_embedding`; no second HTTP embed
   client may be added for search. Extend this instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rugbrain.embeddings import get_query_embedding

#: Outcome labels for :attr:`RetrievalResult.tier`.
TIER_OK = "ok"
TIER_UNAVAILABLE = "unavailable"
#: The labels that mean "a real search ran".
TIER_LABELS = ("speed", "deep")


@dataclass
class RetrievalResult:
    """Outcome of a tiered retrieval pass.

    ``tier`` is one of:
      * ``"speed"`` / ``"deep"`` — the label that served the query
      * ``"unavailable"``          — the embedder could not be reached; no
        search was attempted, ``contexts`` is empty BY DESIGN and ``reason``
        says why
    ``tier == "unavailable"`` and ``tier in (speed, deep)`` with empty
    ``contexts`` are different worlds: infrastructure down vs nothing
    matched. Never collapse them.
    """

    contexts: list[dict[str, Any]] = field(default_factory=list)
    tier: str = TIER_UNAVAILABLE
    fallback_used: bool = False  # True when the primary tier failed
    latency_ms: float = 0.0
    recall_count: int = 0
    reason: str = ""  # populated only when tier == "unavailable"
    collection: str = ""

    @property
    def ok(self) -> bool:
        """True when a real search ran (regardless of hit count)."""
        return self.tier in TIER_LABELS


async def embed_query(text: str) -> list[float]:
    """The ONE embedder seam for retrieval.

    Delegates to :func:`rugbrain.embeddings.get_query_embedding`, so the
    retriever and the content pipeline resolve the same backend chain
    (``RAG_EMBED_BACKEND=auto`` -> DeepInfra -> local :8004 -> fail loud).
    No module may add a second embed client for search; extend this.
    """
    return await get_query_embedding(text)
