"""``GET /api/v1/search`` — semantic search across scanner findings.

One job: the HTTP contract. Retrieve, rate-limit, rerank, rate the results,
merge chain data, reflect, sanitise — each step is delegated to
``search_retrieval`` / ``search_postprocess`` / ``search_chain_data``. The
request's eighteen query parameters are declared in
:mod:`search_pkg.params` and arrive here as one :class:`SearchParams`.

``_analyze_address_findings`` is resolved through the ``rugbrain.endpoints``
module object at CALL TIME so tests monkeypatching
``rugbrain.endpoints._analyze_address_findings`` keep working.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request

from rugbrain import endpoints as _ep
from rugbrain.knowledge_boundary import sanitize_public_result
from rugbrain.routes import search_retrieval as retrieval
from rugbrain.routes.search_chain_data import merge_chain_data
from rugbrain.routes.search_pkg._shared import enforce_rate_limit
from rugbrain.routes.search_pkg.params import SEARCH_DOC, SearchParams
from rugbrain.routes.search_postprocess import apply_ratings, reflect

router: APIRouter = APIRouter()

logger = logging.getLogger(__name__)


@router.get("/search", description=SEARCH_DOC)
async def search_threat_intel(
    request: Request,
    params: SearchParams = Depends(),
) -> dict[str, Any]:
    """Semantic search across scanner findings ingested into Qdrant.

    See ``SEARCH_DOC`` in :mod:`search_pkg.params` for the full contract: the
    docstring is bound as the OpenAPI operation description so the request
    table can live beside the parameter declarations it documents.
    """
    from rugbrain.canary_routing import sanitize_canary_result

    q, limit, collection, rerank = params.q, params.limit, params.collection, params.rerank

    if not q:
        return {"results": [], "query": q, "collection": collection, "total": 0}

    collection = retrieval.resolve_collection(collection)
    canary_collection = retrieval.resolve_canary(collection)

    # Each search performs an embedding + Qdrant query (+ optional rerank), so
    # an unthrottled caller could burn significant compute.
    await enforce_rate_limit(request, "search")

    filters = retrieval.build_filters(**params.filter_kwargs())

    # Hybrid search: dense + BM25 sparse with native Qdrant RRF when the
    # target collection carries a bm25 sparse vector (post sparse-migration
    # canonical collections), falling back to dense + keyword re-rank for
    # dense-only collections. Keeps /api/v1/search serving hybrid results
    # through canonical collection names after the alias swap.
    retrieval_result = await retrieval.retrieve(
        retrieval.RetrievalRequest(
            query=q, collection=collection, limit=limit, rerank=rerank, filters=filters
        ),
        canary_collection,
    )
    results, reranker_applied = await _rerank_results(retrieval_result.results, q, limit, rerank)

    rating = apply_ratings(
        results,
        factuality_filter=params.factuality_filter,
        min_source_tier=params.min_source_tier,
        require_corroboration=params.require_corroboration,
        reranker_applied=reranker_applied,
    )
    results = rating.results

    # Chain-indexer parquet fallback: when the caller opts in (chain_data=true)
    # and either vector search returned nothing or the query is clearly an
    # on-chain data question, merge B2 chain-data rows. Tagged
    # source=chain-data-parquet so consumers can distinguish.
    chain_outcome = merge_chain_data(results, q, limit, params.chain_data)
    results = chain_outcome.results

    pattern_matches = await _address_pattern_matches(q, limit, params.filter_chain)

    # Pre-generation reflection: verify on-chain refs before output.
    reflection = await reflect(q, results)

    if canary_collection is not None:
        results = [sanitize_canary_result(result, collection) for result in results[:limit]]
    else:
        results = [sanitize_public_result(result) for result in results[:limit]]
    if pattern_matches is not None:
        pattern_matches = sanitize_public_result(pattern_matches)
    return {
        "results": results,
        "query": q,
        "collection": collection,
        "total": len(results),
        "reflection": reflection,
        "filters_applied": filters is not None,
        "reranker_applied": reranker_applied,
        "rating_filters_applied": rating.filters_applied,
        "rating_boosts_applied": rating.boosts_applied,
        "chain_data_applied": chain_outcome.applied,
        "chain_data_intent": chain_outcome.intent,
        "chain_data_error": chain_outcome.error,
        "pattern_matches": pattern_matches,
        # Market context for scoring: fearful market → higher risk on new
        # tokens. Populated for token-address queries (see pattern_matches).
        "fear_index": (pattern_matches or {}).get("fear_index"),
    }


async def _rerank_results(
    results: list[dict[str, Any]], q: str, limit: int, rerank: bool
) -> tuple[list[dict[str, Any]], bool]:
    """Re-sort by cross-encoder score. Fail-open: a rerank error keeps order."""
    if not (rerank and results):
        return results, False
    try:
        from rugbrain.reranker import rerank as _rerank

        docs = [
            str(r.get("content") or r.get("text") or r.get("title") or r.get("summary") or "")[
                :2000
            ]
            for r in results
        ]
        reranked = await _rerank(q, docs, top_k=limit)
        if not reranked:
            return results, False
        for r in reranked:
            idx = r.get("index")
            if isinstance(idx, int) and 0 <= idx < len(results):
                results[idx]["rerank_score"] = r.get("relevance_score", 0.0)
                results[idx]["reranked"] = True
        results.sort(key=lambda r: r.get("rerank_score", 0.0), reverse=True)
        return results, True
    except Exception as exc:
        logger.debug("search rerank failed (non-critical): %s", exc)
        return results, False


async def _address_pattern_matches(
    q: str, limit: int, filter_chain: str | None
) -> dict[str, Any] | None:
    """Scam-pattern verdict for a bare token/contract address query, else None.

    Fail-open: never break the search on analysis errors.
    """
    if not _ep._is_token_address(q):
        return None
    return await _ep._analyze_address_findings(
        q.strip().lower(), chain=filter_chain or "ethereum", limit=min(limit, 25)
    )
