"""The tiered retriever itself — routing, guard, search, rerank.

Extracted from :mod:`rugbrain.search.dual_tier` (2026-09-29); that module
keeps the root-cause write-up and the re-export surface, this one has the
machinery. The two contracts that make a dead lane loud are enforced HERE:

* :func:`guard_query_dim` is called on every search and RAISES on a dim
  mismatch. It is never converted into an empty candidate list, because an
  empty list is indistinguishable from a real miss.
* An unreachable embedder produces ``tier="unavailable"`` with a populated
  ``reason`` (see :class:`rugbrain.search.tiers.RetrievalResult`), never a
  zero-hit ``"ok"``.

Both lanes — embedder and reranker — resolve through :func:`_lane`, which
looks the name up in this module's ``globals()`` on every call. An
import-time snapshot is what let the original bug hide: a renamed or
patched-out name left the call site running the old object with no error.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from rugbrain.embeddings import RAGUnavailableError
from rugbrain.search.candidates import to_candidates
from rugbrain.search.dim_guard import guard_query_dim
from rugbrain.search.lane_targets import resolve_search_collection
from rugbrain.search.rerank import apply_instruction, rerank_chunks
from rugbrain.search.tiers import TIER_UNAVAILABLE, RetrievalResult, embed_query

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

#: tier label -> (named vector to search, recall depth)
TIER_PLAN: dict[str, tuple[str | None, int]] = {
    "speed": (None, SPEED_RECALL),
    "deep": (None, DEEP_RECALL),
}


def _lane(name: str, default: Any) -> Any:
    """Resolve a lane at CALL time from this module's globals.

    Import-time snapshots are how the old bug class returned: a name
    imported once and then patched out from under the module (or renamed in
    the source) left the call site running the OLD object with no error.
    Looking the name up on every call means a patch, a rename, or a
    re-export is visible immediately, and a missing name raises NameError
    instead of silently degrading to a dead lane.
    """
    return globals().get(name, default)


def pick_tier(query: str) -> str:
    """Route by query complexity: short consumer queries -> speed tier."""
    return "speed" if len(query.split()) < ROUTING_THRESHOLD_WORDS else "deep"


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
        embedder = self._embedder or _lane("embed_query", embed_query)
        return await embedder(text)

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
        return await self._rank(inst_query, to_candidates(hits.points), tier)

    async def _rank(
        self, inst_query: str, candidates: list[dict[str, Any]], tier: str
    ) -> list[dict[str, Any]]:
        rerank = self._reranker or _lane("rerank_chunks", rerank_chunks)
        return await rerank(
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
        coll = _resolve_collection(collection)
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


def _resolve_collection(collection: str | None) -> str:
    """Resolve to the name the serving lane populated, BEFORE the guard reads it.

    ``COLLECTION`` defaults to the base ``rag_documents``; querying that base
    at 4096 dims is the exact mismatch :func:`guard_query_dim` would then have
    to report as a collection fault. Resolving first makes the guard check the
    collection we actually intend to read.
    """
    return resolve_search_collection(collection or COLLECTION) or COLLECTION
