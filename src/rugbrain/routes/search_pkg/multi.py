"""``POST /api/v1/search/multi`` — multi-collection search fused with RRF.

Queries each requested collection independently, fuses with Reciprocal Rank
Fusion (k=60), and boosts ``threat_intel_v2`` / ``scam_intel_v2`` by 20%.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from rugbrain.knowledge_boundary import sanitize_public_result
from rugbrain.routes.search_pkg._shared import (
    RRF_K as _RRF_K,
)
from rugbrain.routes.search_pkg._shared import (
    MultiSearchRequest,
    enforce_rate_limit,
    resolve_public_collections,
)

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter()


@router.post("/search/multi")
async def search_multi(
    request: Request,
    body: MultiSearchRequest,
) -> dict[str, Any]:
    """Search across multiple Qdrant collections in parallel, fuse with RRF.

    Queries each collection independently, then fuses results via
    Reciprocal Rank Fusion (RRF, k=60). Boosts results from ``threat_intel_v2``
    and ``scam_intel_v2`` by 20% (higher priority sources).

    When ``rerank=true``, the top-40 fused candidates are scored by the
    self-hosted Qwen3-Reranker-8B cross-encoder before truncating to ``limit``.

    Example::

        curl -X POST "http://localhost:8012/api/v1/search/multi" \\
          -H "Content-Type: application/json" \\
          -H "X-API-Key: ..." \\
          -d '{"query":"flash loan attack","collections":["threat_intel_v2","scam_patterns"],"rerank":true}'
    """
    from rugbrain.canary_routing import CanaryConfigError, canary_target_for
    from rugbrain.embeddings import get_query_embedding
    from rugbrain.qdrant_store import QdrantVectorStore

    q = body.query
    cols = body.collections
    lim = body.limit
    per_col = body.limit_per_collection
    do_rerank = body.rerank

    if not q:
        return {"results": [], "query": q, "collections": cols, "total": 0}

    cols = resolve_public_collections(cols)

    try:
        canary_targets = [canary_target_for(col) for col in cols]
    except CanaryConfigError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "canary_configuration_invalid"},
        ) from exc
    if any(target is not None for target in canary_targets):
        raise HTTPException(
            status_code=400,
            detail={"error": "canary_not_supported_for_multi"},
        )

    # Rate limit by client IP
    await enforce_rate_limit(request, "search")

    try:
        embedding = await get_query_embedding(q)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={"error": "embedding_unavailable", "message": str(e)[:200]},
        ) from e

    # Search each collection in parallel. The names the caller sent are
    # resolved to the lane the serving lane actually populates, exactly as
    # ``/search`` and the write path do (be0da01) — querying a base name here
    # was the same incompatible-schema defect one route over. The RESPONSE
    # still reports the caller's names, and the priority boost below still
    # keys off them, so the envelope is unchanged.
    from rugbrain.search.lane_targets import resolve_search_collection

    store = QdrantVectorStore()
    tasks = [
        store.hybrid_search(
            query_text=q,
            query_embedding=embedding,
            collection=resolve_search_collection(col),
            limit=per_col,
            min_similarity=0.4,
        )
        for col in cols
    ]
    per_collection = await asyncio.gather(*tasks, return_exceptions=True)

    # RRF fusion: collect results from each collection, score by RRF
    rrf_scores: dict[str, dict[str, Any]] = {}
    for idx, results in enumerate(per_collection):
        if isinstance(results, Exception):
            logger.debug("search_multi collection=%s failed: %s", cols[idx], results)
            continue
        results_list: list[dict[str, Any]] = results  # type: ignore[assignment]
        for rank, hit in enumerate(results_list):
            doc_id = hit.get("doc_id") or hit.get("id") or str(hit.get("payload", {}).get("id", ""))
            if not doc_id:
                continue
            rrf_score = 1.0 / (_RRF_K + rank + 1)
            # Boost high-priority collections
            coll = cols[idx]
            if coll in ("threat_intel_v2", "scam_intel_v2"):
                rrf_score *= 1.2
            if doc_id in rrf_scores:
                rrf_scores[doc_id]["rrf_score"] += rrf_score
            else:
                hit["rrf_score"] = rrf_score
                hit["_source_collections"] = [coll]
                rrf_scores[doc_id] = hit

    # Sort by RRF score descending
    fused = sorted(rrf_scores.values(), key=lambda r: r.get("rrf_score", 0.0), reverse=True)

    # Optional rerank
    reranker_applied = False
    if do_rerank and fused:
        try:
            from rugbrain.reranker import rerank as _rerank

            top_n = min(len(fused), 40)
            docs = []
            for r in fused[:top_n]:
                txt = r.get("content") or r.get("text") or r.get("title") or r.get("summary") or ""
                docs.append(str(txt)[:2000])
            reranked = await _rerank(q, docs, top_k=min(top_n, lim))
            if reranked:
                for r in reranked:
                    r_idx = r.get("index")
                    if isinstance(r_idx, int) and 0 <= r_idx < len(docs):
                        fused[r_idx]["rerank_score"] = r.get("relevance_score", 0.0)
                        fused[r_idx]["reranked"] = True
                fused[:top_n] = sorted(
                    fused[:top_n], key=lambda r: r.get("rerank_score", 0.0), reverse=True
                )
                reranker_applied = True
        except Exception as e:
            logger.debug("search_multi rerank failed (non-critical): %s", e)

    return {
        "results": [sanitize_public_result(result) for result in fused[:lim]],
        "query": q,
        "collections": cols,
        "total": len(fused[:lim]),
        "reranker_applied": reranker_applied,
    }
