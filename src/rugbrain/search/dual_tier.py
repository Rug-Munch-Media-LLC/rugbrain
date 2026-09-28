"""Tier-labeled retrieval over the ONE canonical embedder (2026-09-28).

ROOT CAUSE FIXED HERE: this module used to own a private HTTP embed client
pointing at ``QWEN_EMBED_URL`` (local :8004). That lane is MASKED and dead,
so every query produced ``both tiers unavailable`` and ZERO documents while
the rest of the fleet — which embeds through
:func:`rugbrain.embeddings.get_query_embedding` — worked fine. Two code
paths disagreed about which embedder was live, and the disagreement was
invisible because the failure degraded to an empty result set.

Now there is ONE embedder seam (:func:`embed_query`) and it delegates to
``get_query_embedding``; nothing here talks HTTP to an embedder. The tier
labels are kept for API compatibility: the 0.6B "speed" tier was retired
(2026-08-29) and its endpoints removed (2026-09-14), so both labels resolve
to the same canonical qwen3-embedding-4b lane.

Failures are EXPLICIT, never silent:

* :class:`EmbeddingDimMismatch` / :class:`UnknownCollection` propagate —
  a wrong-dimension query raises instead of returning ``[]``.
* When the embedder is genuinely unreachable, :meth:`retrieve` returns
  ``tier="unavailable"`` with a populated :attr:`RetrievalResult.reason`.
  ``tier="unavailable"`` is distinguishable from ``tier="ok"`` with zero
  hits, so "the embedder is down" can never read as "nothing matched".

Rerank is unchanged in spirit: candidates go through
:func:`rugbrain.search.rerank.rerank_chunks`, which keeps its documented
fail-open (reranker down -> original order, tagged ``rerank_method="none"``).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rugbrain.embeddings import RAGUnavailableError, get_query_embedding
from rugbrain.search.dim_guard import (
    EmbeddingDimMismatch,
    guard_query_dim,
)
from rugbrain.search.lane_targets import resolve_search_collection
from rugbrain.search.rerank import apply_instruction, rerank_chunks

# ── configuration ────────────────────────────────────────────────────────────
#: Default collection. ``crypto_intelligence`` was renamed to ``rag_documents``
#: in the 2026-09-20 consolidation, so the old default pointed at a collection
#: that no longer exists. RAG_COLLECTION still wins when set.
COLLECTION = os.getenv("RAG_COLLECTION", "rag_documents")
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")

ROUTING_THRESHOLD_WORDS = int(os.getenv("ROUTING_THRESHOLD_WORDS", "45"))
SPEED_RECALL = int(os.getenv("SPEED_RECALL", "80"))
DEEP_RECALL = int(os.getenv("DEEP_RECALL", "30"))
TOP_CONTEXT = int(os.getenv("TOP_CONTEXT", "5"))
HTTP_TIMEOUT_S = float(os.getenv("DUAL_TIER_TIMEOUT", "60"))

#: Payload fields that can carry a document's readable body, in precedence
#: order. The retriever used to read only ``text``, which exists in almost no
#: real collection — the corpus stores ``content`` — so every context came
#: back with an EMPTY body. Downstream graders (missioneval's judge reads
#: ``content``/``title``/``description``) therefore saw empty text and scored
#: real hits as misses: the same silent-degradation class of bug.
PAYLOAD_TEXT_KEYS: tuple[str, ...] = ("content", "text", "body", "chunk_text", "title")


def _payload_text(payload: dict[str, Any]) -> str:
    """First non-empty readable field in a Qdrant payload."""
    for key in PAYLOAD_TEXT_KEYS:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


#: tier label -> (named vector to search, recall depth)
TIER_PLAN: dict[str, tuple[str | None, int]] = {
    "speed": (None, SPEED_RECALL),
    "deep": (None, DEEP_RECALL),
}

#: Outcome labels for :attr:`RetrievalResult.tier`.
TIER_OK = "ok"
TIER_UNAVAILABLE = "unavailable"


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
        return self.tier in ("speed", "deep")


def pick_tier(query: str) -> str:
    """Route by query complexity: short consumer queries -> speed tier."""
    return "speed" if len(query.split()) < ROUTING_THRESHOLD_WORDS else "deep"


async def embed_query(text: str) -> list[float]:
    """The ONE embedder seam for retrieval.

    Delegates to :func:`rugbrain.embeddings.get_query_embedding`, so the
    retriever and the content pipeline resolve the same backend chain
    (``RAG_EMBED_BACKEND=auto`` -> DeepInfra -> local :8004 -> fail loud).
    No module may add a second embed client for search; extend this.
    """
    return await get_query_embedding(text)


class DualTierRetriever:
    """Word-count routed retrieval over the canonical embedder/rerank tiers."""

    def __init__(
        self,
        qdrant: Any = None,
        embedder: Callable[[str], Any] | None = None,
        reranker: Callable[..., Any] | None = None,
    ) -> None:
        self._qdrant = qdrant
        #: DI seam. ``None`` -> the canonical seam (embed_query).
        self._embedder = embedder
        #: DI seam. ``None`` -> rugbrain.search.rerank.rerank_chunks.
        self._reranker = reranker

    # ── internals ─────────────────────────────────────────────────────────
    def _qdrant_client(self) -> Any:
        if self._qdrant is None:
            from qdrant_client import AsyncQdrantClient

            self._qdrant = AsyncQdrantClient(url=QDRANT_URL)
        return self._qdrant

    async def _embed(self, text: str) -> list[float]:
        if self._embedder is not None:
            return await self._embedder(text)
        return await embed_query(text)

    async def _tier_retrieve(
        self,
        tier: str,
        inst_query: str,
        limit: int,
        collection: str,
    ) -> list[dict[str, Any]]:
        qdrant = self._qdrant_client()
        vector_name, _ = TIER_PLAN[tier]
        vec = await self._embed(inst_query)
        # THE GUARD: raises on query-dim drift or collection-dim drift. This
        # is the line that turns the 4096-vs-2560 silent empty result into a
        # named exception.
        await guard_query_dim(qdrant, collection, vec, vector_name)
        hits = await qdrant.query_points(
            collection_name=collection, query=vec, using=vector_name, limit=limit
        )
        candidates = [
            {
                # BOTH keys are populated deliberately: `content` is what the
                # mission-eval judge and the rerank text extractor read, and
                # `text` keeps the historical dual_tier contract for callers
                # that were written against it.
                "text": _payload_text(p.payload or {}),
                "content": _payload_text(p.payload or {}),
                "meta": p.payload.get("metadata", {}),
                "id": str(p.id),
            }
            for p in hits.points
        ]
        return await self._rank(inst_query, candidates, tier)

    async def _rank(
        self, inst_query: str, candidates: list[dict[str, Any]], tier: str
    ) -> list[dict[str, Any]]:
        if self._reranker is not None:
            return await self._reranker(inst_query, candidates, tier)
        return await rerank_chunks(
            inst_query, candidates, top_n=len(candidates), keep=TOP_CONTEXT, tier=tier
        )

    # ── public API ────────────────────────────────────────────────────────
    async def retrieve(
        self, query: str, top_k: int = TOP_CONTEXT, collection: str | None = None
    ) -> RetrievalResult:
        """Route a query across the tiers and return ranked contexts.

        Contract:

        * ``tier="speed"``/``"deep"`` — a real search ran. ``contexts`` may
          legitimately be empty; that is a MISS, not an outage.
        * ``tier="unavailable"`` — the embedder was unreachable. ``reason``
          names the cause and no search was attempted.
        * Dimension drift RAISES (:class:`EmbeddingDimMismatch`,
          :class:`UnknownCollection`). It is never converted into an empty
          result, because an empty result is indistinguishable from a miss.
        """
        t0 = time.perf_counter()
        primary = pick_tier(query)
        inst_query = apply_instruction(query)
        # Resolve to the name the serving lane populated, BEFORE the guard reads
        # the collection and BEFORE query_points names it. `COLLECTION` defaults
        # to the base `rag_documents`; querying that base at 4096 dims is the
        # exact mismatch this module's guard would then have to report as a
        # collection fault. Resolving first makes the guard check the collection
        # we actually intend to read.
        coll = resolve_search_collection(collection or COLLECTION) or COLLECTION
        limit = TIER_PLAN[primary][1]

        try:
            contexts = await self._tier_retrieve(primary, inst_query, limit, coll)
        except (RAGUnavailableError, OSError, TimeoutError, ConnectionError) as exc:
            return RetrievalResult(
                tier=TIER_UNAVAILABLE,
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                reason=f"embedder_unavailable: {type(exc).__name__}: {exc}",
                collection=coll,
            )
        return RetrievalResult(
            contexts=contexts[:top_k],
            tier=primary,
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            recall_count=len(contexts),
            collection=coll,
        )

    async def ask(self, query: str) -> dict[str, Any]:
        """Convenience: retrieve only. Generation is a separate lane.

        Generation is NOT implemented here: the chat lane (:8009) is outside
        this module's job (one job per module) and a mixed
        retrieve+generate helper would re-introduce a second, unguarded
        client path.
        """
        result = await self.retrieve(query)
        return {
            "contexts": result.contexts,
            "tier": result.tier,
            "ok": result.ok,
            "reason": result.reason,
            "fallback_used": result.fallback_used,
            "latency_ms": result.latency_ms,
        }


__all__ = [
    "COLLECTION",
    "TIER_OK",
    "TIER_PLAN",
    "TIER_UNAVAILABLE",
    "DualTierRetriever",
    "EmbeddingDimMismatch",
    "RetrievalResult",
    "embed_query",
    "pick_tier",
]
