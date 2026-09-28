"""Retrieval for ``GET /api/v1/search`` — embed, filter, fetch.

One job: turn a validated query plus its filters into retrieved result dicts.
Everything after retrieval (rerank, ratings, chain-data, reflection, public
sanitisation) is the route's or another module's concern, not this one's.

All Qdrant/embedding access is confined here, so the route module keeps no
storage imports of its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from rugbrain.knowledge_boundary import CollectionNotPublic, require_public_collection

logger = logging.getLogger(__name__)

#: Candidates fetched before optional reranking, as a multiple of the limit.
CANDIDATE_MULTIPLE = 4
MIN_CANDIDATES = 20
MAX_CANDIDATES = 100
MIN_SIMILARITY = 0.4


@dataclass
class RetrievalRequest:
    """The validated shape a retrieval needs. Built by the route."""

    query: str
    collection: str
    limit: int
    rerank: bool
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    """Retrieved rows plus the collection they came from."""

    results: list[dict[str, Any]]
    collection: str


def build_filters(**kwargs: Any) -> dict[str, Any]:
    """Translate query params into a Qdrant filter payload."""
    from rugbrain.qdrant_store import build_search_filters

    return build_search_filters(collection=None, **kwargs)


def resolve_collection(collection: str) -> str:
    """Reject any collection outside the public allowlist (403)."""
    try:
        return require_public_collection(collection)
    except CollectionNotPublic as exc:
        raise HTTPException(
            status_code=403, detail={"error": "collection_not_available"}
        ) from exc


def resolve_canary(collection: str) -> str | None:
    """Map a collection onto its canary target, if one is configured (400)."""
    from rugbrain.canary_routing import CanaryConfigError, canary_target_for

    try:
        return canary_target_for(collection)
    except CanaryConfigError as exc:
        raise HTTPException(
            status_code=400, detail={"error": "canary_configuration_invalid"}
        ) from exc


async def embed(query: str, canary_collection: str | None) -> list[float]:
    """Embed the query, honouring the canary embedding lane (503 on failure)."""
    from rugbrain.canary_routing import (
        CanaryConfigError,
        CanaryUnavailable,
        get_canary_query_embedding,
        validate_canary_vector,
    )
    from rugbrain.embeddings import get_query_embedding

    try:
        if canary_collection is not None:
            return validate_canary_vector(await get_canary_query_embedding(query))
        return await get_query_embedding(query)
    except (CanaryConfigError, CanaryUnavailable) as exc:
        raise HTTPException(
            status_code=503, detail={"error": "canary_embedding_unavailable"}
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "embedding_unavailable", "message": str(exc)[:200]},
        ) from exc


async def retrieve(req: RetrievalRequest, canary_collection: str | None) -> RetrievalResult:
    """Embed, then run the hybrid dense+BM25 search against the store."""
    from rugbrain.qdrant_store import QdrantVectorStore

    embedding = await embed(req.query, canary_collection)
    target = canary_collection or req.collection
    candidate_limit = (
        min(max(req.limit * CANDIDATE_MULTIPLE, MIN_CANDIDATES), MAX_CANDIDATES)
        if req.rerank
        else req.limit
    )
    store = QdrantVectorStore()
    results = await store.hybrid_search(
        query_text=req.query,
        query_embedding=embedding,
        collection=target,
        limit=candidate_limit,
        min_similarity=MIN_SIMILARITY,
        filters=req.filters,
        **({"allow_create": False} if canary_collection is not None else {}),
    )
    return RetrievalResult(list(results), req.collection)
