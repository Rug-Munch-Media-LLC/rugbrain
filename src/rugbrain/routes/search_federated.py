"""Federated multi-backend search route.

GET /api/v1/search/federated?q=...&collections=news_v2_i,threat_intel_v2_i
    Fans the query across every backend mapped to the requested collections
    (qwen4b / qwen06b / nemotron-free / any OpenRouter embed model), merges
    with reciprocal-rank fusion, tags each hit with its backend provenance.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

logger = logging.getLogger("rugbrain.search_federated")

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.get("/federated")
async def federated_search(
    q: str = Query(..., min_length=2, description="query text"),
    collections: str = Query(
        default="news_v2_i,threat_intel_v2_i",
        description="comma-separated collections (each routed by its backend map entry)",
    ),
    limit: int = Query(default=10, ge=1, le=50),
    rerank: str = Query(
        default="fast",
        pattern="^(fast|deep|off)$",
        description="rerank lane: fast=0.6B consumer (:8008), deep=4B (:8003), off",
    ),
) -> dict[str, Any]:
    from rugbrain.multi_embedder import get_multi_embedder

    colls = [c.strip() for c in collections.split(",") if c.strip()]
    me = get_multi_embedder()
    results = await me.federated_search(q, colls, limit_per=limit, rerank_tier=rerank)
    return {
        "query": q,
        "collections": colls,
        "results": results[:limit],
        "total": len(results),
        "backends_used": sorted({r["backend"] for r in results}),
    }


def _qdrant_base() -> str:
    import os

    return os.getenv("QDRANT_URL", os.getenv("QDRANT_FALLBACK", "http://127.0.0.1:6334")).rstrip(
        "/"
    )


@router.get("/vector-landscape")
async def vector_landscape() -> dict[str, Any]:
    """Live map of EVERY vector space in the fleet.

    For each Qdrant instance: every collection's actual dim + point count,
    the inferred producing backend (2560=qwen4b, 2048=nemotron,
    1024=legacy-trunc/qwen06b), the configured resolution, and MISMATCH
    flags where searches would fail. The single answer to 'what vectors do
    we have and can we query them all?'
    """
    from rugbrain.multi_embedder import get_multi_embedder

    me = get_multi_embedder()
    return await me.landscape()
