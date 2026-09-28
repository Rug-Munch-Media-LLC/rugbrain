"""Retrieval-stage capability shims from `app.*`, now in one place.

Standalone RugBrain does its retrieval natively in Qdrant, so the ANN index,
semantic cache, knowledge graph, parent retriever and query transform are all
stand-ins. They are silent by design — a missing cache or an absent graph must
not fail a query — but each is a real capability gap: with ``SemanticCache``
inert, every query re-runs the full embed → search → rerank path; with
``AnnIndex.search`` returning ``[]`` and ``mmr_dedup_results`` a pass-through,
dedup and pruning are absent from results.

``get_reranker`` is the exception: it delegates to the real
:mod:`rugbrain.reranker` when that module is importable.
"""

from __future__ import annotations

from typing import Any


# ── ANN Index ─────────────────────────────────────────────────────
class AnnIndex:
    """Placeholder ANN index that uses Qdrant directly."""

    _instace: AnnIndex | None = None

    def __new__(cls) -> AnnIndex:
        if cls._instace is None:
            cls._instace = super().__new__(cls)
        return cls._instace

    def is_built(self, collection: str) -> bool:
        return True  # Qdrant handles indexing natively

    async def search(
        self, query_embedding: list[float], collection: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        return []

    async def bump_version(self, collection: str) -> None:
        """No-op version bump — Qdrant manages its own index state."""

    async def stats(self) -> dict[str, Any]:
        """No-op stats — Qdrant collection stats are served by get_stats()."""
        return {}


def get_ann_index() -> AnnIndex:
    return AnnIndex()


# ── Semantic Cache ───────────────────────────────────────────────
class SemanticCache:
    """No-op semantic cache — bypasses caching for standalone mode."""

    async def check(self, query_vec: list[float]) -> Any | None:
        return None

    async def store(self, query_vec: list[float], results: list[dict[str, Any]]) -> None:
        pass

    async def stats(self) -> dict[str, Any]:
        return {"enabled": False, "hits": 0, "misses": 0}


def get_semantic_cache() -> SemanticCache:
    return SemanticCache()


# ── Knowledge Graph ──────────────────────────────────────────────
async def get_knowledge_graph() -> Any | None:
    return None


# ── MMR Dedup ────────────────────────────────────────────────────
async def mmr_dedup_results(results: list[dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
    return results


# ── Cross-encoder Reranker ───────────────────────────────────────
async def get_reranker() -> Any | None:
    try:
        from rugbrain.reranker import get_reranker as _get_reranker

        return _get_reranker()  # sync factory returning a Reranker instance
    except Exception:
        return None


# ── Parent Retriever ─────────────────────────────────────────────
async def get_parent_retriever() -> Any | None:
    return None


# ── Query Transform ──────────────────────────────────────────────
class QueryTransformResult:
    """Result shape expected by search_with_transform (pass-through)."""

    def __init__(self, query: str, strategy: str = "auto") -> None:
        self.transformed_queries: list[str] = [query]
        self.strategy = strategy
        self.metadata: dict[str, Any] = {"transform": "passthrough"}


async def transform_query(query: str, **kwargs: Any) -> QueryTransformResult:
    return QueryTransformResult(query, strategy=str(kwargs.get("strategy", "auto")))
