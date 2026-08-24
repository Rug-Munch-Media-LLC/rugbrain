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
) -> dict[str, Any]:
    from rugbrain.multi_embedder import get_multi_embedder

    colls = [c.strip() for c in collections.split(",") if c.strip()]
    me = get_multi_embedder()
    results = await me.federated_search(q, colls, limit_per=limit)
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
