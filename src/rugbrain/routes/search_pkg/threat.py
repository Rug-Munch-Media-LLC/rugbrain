"""``GET /api/v1/search`` — semantic search across scanner findings.

Retrieval, ratings, chain-data fallback, and reflection are delegated to
``search_retrieval`` / ``search_postprocess`` / ``search_chain_data``; this
module owns only the HTTP contract and the response envelope."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from typing import Any

from rugbrain import endpoints as _ep
from rugbrain.knowledge_boundary import sanitize_public_result
from rugbrain.routes import search_retrieval as retrieval
from rugbrain.routes.search_chain_data import merge_chain_data
from rugbrain.routes.search_pkg._shared import enforce_rate_limit
from rugbrain.routes.search_postprocess import apply_ratings, reflect

router: APIRouter = APIRouter(tags=["rag-optimization"])

logger = logging.getLogger(__name__)

@router.get("/search")
async def search_threat_intel(
    request: Request,
    q: str = Query(
        default="", description="Search query — token address, name, or scam description"
    ),
    limit: int = Query(default=10, ge=1, le=100),
    collection: str = Query(default="threat_intel_v2", description="Qdrant collection to search"),
    rerank: bool = Query(
        default=False,
        description="Rerank top-20 candidates with the self-hosted Qwen3-Reranker-4B cross-encoder",
    ),
    filter_severity: str | None = Query(
        default=None,
        description="Exact-match payload filter: severity (critical|high|medium|low|info)",
    ),
    filter_chain: str | None = Query(
        default=None,
        description="Exact-match payload filter: chain (ethereum, bsc, solana, multi, ...)",
    ),
    filter_attack_vector: str | None = Query(
        default=None,
        description="Exact-match payload filter: attack_vector (reentrancy, flash-loan, ...)",
    ),
    filter_category: str | None = Query(
        default=None,
        description="Exact-match payload filter: category (smart-contract, phishing, ...)",
    ),
    filter_protocol: str | None = Query(
        default=None,
        description="Exact-match payload filter: affected protocol name",
    ),
    filter_source_type: str | None = Query(
        default=None,
        description="Exact-match payload filter: source_type (rekt_news, defihacklabs, ...)",
    ),
    filter_date_after: str | None = Query(
        default=None,
        description="Range filter: only points with date >= this ISO date (YYYY-MM-DD)",
    ),
    filter_date_before: str | None = Query(
        default=None,
        description="Range filter: only points with date <= this ISO date (YYYY-MM-DD)",
    ),
    filter_amount_min: float | None = Query(
        default=None,
        ge=0,
        description="Range filter: only points with amount_lost_usd >= this value",
    ),
    filter_amount_max: float | None = Query(
        default=None,
        ge=0,
        description="Range filter: only points with amount_lost_usd <= this value",
    ),
    factuality_filter: str | None = Query(
        default=None,
        pattern="^(verified|reported|rumor)$",
        description="Rating filter: only articles with this factuality rating (verified|reported|rumor)",
    ),
    min_source_tier: str | None = Query(
        default=None,
        pattern="^(established|independent)$",
        description="Rating filter: only articles with at least this source reputation tier (established|independent)",
    ),
    require_corroboration: bool = Query(
        default=False,
        description="Rating filter: only articles corroborated by >= 3 independent sources",
    ),
    chain_data: bool = Query(
        default=False,
        description=(
            "Fall back to chain-indexer parquet (B2 chain-data) when vector "
            "results are empty or the query asks an on-chain data question "
            "(e.g. 'recent ETH transfers', 'who deployed tokens on Base yesterday'). "
            "Results are tagged with source=chain-data-parquet."
        ),
    ),
) -> dict[str, Any]:
    """
    Semantic search across scanner findings ingested into Qdrant.

    Queries the ``threat_intel_v2`` collection (populated in real-time by the
    scanner subscriber) for tokens that match the query by semantic similarity.

    When ``rerank=true``, the top-20 candidates from vector search are scored
    by the self-hosted Qwen3-Reranker-4B cross-encoder and re-sorted before truncating
    to ``limit``. If the reranker backend is unavailable, results fall back
    to the original vector ranking (search never breaks).

    Crypto-specific payload filters combine with hybrid search — Qdrant
    applies them *before* fusion, so filtered results are still dense+BM25
    fused. Exact-match filters: ``filter_severity``, ``filter_chain``,
    ``filter_attack_vector``, ``filter_category``, ``filter_protocol``,
    ``filter_source_type``. Range filters: ``filter_date_after`` /
    ``filter_date_before`` (ISO dates, compared on the ``date_ts`` timestamp
    payload field) and ``filter_amount_min`` / ``filter_amount_max`` (USD,
    compared on ``amount_lost_usd``).

    Ground News-style rating filters (applied post-search): ``factuality_filter``
    (verified|reported|rumor), ``min_source_tier`` (established|independent),
    and ``require_corroboration`` (>= 3 independent sources). When any rating
    filter is supplied, results whose rating conflicts are dropped, and the
    survivors are reweighted so verified/corroborated articles rank higher.

    Example::

        curl "http://localhost:8012/api/v1/search?q=reentrancy&collection=threat_intel_v2&filter_severity=critical&filter_chain=ethereum&filter_date_after=2023-01-01&limit=5&factuality_filter=verified&require_corroboration=1"
    """
    from rugbrain.canary_routing import sanitize_canary_result

    if not q:
        return {"results": [], "query": q, "collection": collection, "total": 0}

    collection = retrieval.resolve_collection(collection)
    canary_collection = retrieval.resolve_canary(collection)

    # Each search performs an embedding + Qdrant query (+ optional rerank), so
    # an unthrottled caller could burn significant compute.
    await enforce_rate_limit(request, "search")

    filters = retrieval.build_filters(
        severity=filter_severity,
        chain=filter_chain,
        attack_vector=filter_attack_vector,
        category=filter_category,
        protocol=filter_protocol,
        source_type=filter_source_type,
        collection=None,
        date_after=filter_date_after,
        date_before=filter_date_before,
        amount_min=filter_amount_min,
        amount_max=filter_amount_max,
    )

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
    results = retrieval_result.results

    reranker_applied = False
    if rerank and results:
        try:
            from rugbrain.reranker import rerank as _rerank

            # Build document strings from result dicts (content/text/title).
            docs = []
            for r in results:
                txt = r.get("content") or r.get("text") or r.get("title") or r.get("summary") or ""
                docs.append(str(txt)[:2000])
            reranked = await _rerank(q, docs, top_k=limit)
            if reranked:
                # Map reranker scores back onto the result dicts.
                for r in reranked:
                    idx = r.get("index")
                    if isinstance(idx, int) and 0 <= idx < len(results):
                        results[idx]["rerank_score"] = r.get("relevance_score", 0.0)
                        results[idx]["reranked"] = True
                results.sort(key=lambda r: r.get("rerank_score", 0.0), reverse=True)
                reranker_applied = True
        except Exception as e:
            logger.debug("search rerank failed (non-critical): %s", e)

    # ── Ground News-style rating filters + boosts ──
    # Optional post-search filters drop articles whose rating conflicts with
    # the requested quality bar. Rating boosts then reweight the survivors so
    # verified/corroborated news ranks above unrated or rumor content.
    rating = apply_ratings(
        results,
        factuality_filter=factuality_filter,
        min_source_tier=min_source_tier,
        require_corroboration=require_corroboration,
        reranker_applied=reranker_applied,
    )
    results = rating.results
    rating_filters_applied = rating.filters_applied
    rating_boosts_applied = rating.boosts_applied

    # ── Chain-data parquet fallback ──
    # When the caller opts in (chain_data=true) and either (a) vector search
    # returned nothing or (b) the query is clearly an on-chain data question
    # ("recent ETH transfers", "who deployed tokens on Base yesterday"), fall
    # back to the indexer's B2 chain-data parquet via the shared DuckDB path.
    # Tagged with source=chain-data-parquet so consumers can distinguish.
    chain_outcome = merge_chain_data(results, q, limit, chain_data)
    results = chain_outcome.results
    chain_data_applied = chain_outcome.applied
    chain_data_intent = chain_outcome.intent
    chain_data_error = chain_outcome.error

    # ── Historical pattern matching (token-address queries) ──
    # When the caller searches a bare token/contract address, augment the
    # vector results with the scam-pattern verdict + historical
    # cross-reference. Fail-open: never break the search on analysis errors.
    pattern_matches: dict[str, Any] | None = None
    if _ep._is_token_address(q):
        chain_hint = filter_chain or "ethereum"
        pattern_matches = await _ep._analyze_address_findings(
            q.strip().lower(), chain=chain_hint, limit=min(limit, 25)
        )

    # ── Pre-generation reflection: verify on-chain refs before output ──
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
        "rating_filters_applied": rating_filters_applied,
        "rating_boosts_applied": rating_boosts_applied,
        "chain_data_applied": chain_data_applied,
        "chain_data_intent": chain_data_intent,
        "chain_data_error": chain_data_error,
        "pattern_matches": pattern_matches,
        # Market context for scoring: fearful market → higher risk on new
        # tokens. Populated for token-address queries (see pattern_matches).
        "fear_index": (pattern_matches or {}).get("fear_index"),
    }
