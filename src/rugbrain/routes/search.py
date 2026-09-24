"""Search routes: ``/search``, ``/search/multi`` + the ``scam_check_router``.

Split out of ``rugbrain/endpoints.py`` (degodding phase 3, 2026-08-21).
No URL prefix on ``router`` — the parent router in ``endpoints.py``
supplies ``/api/v1``. ``scam_check_router`` keeps its own
``/api/v1/rag/v2`` prefix and is mounted separately by ``main.py`` (via
the ``rugbrain.endpoints`` re-export), exactly as before.

``_analyze_address_findings`` is resolved through the ``rugbrain.
endpoints`` module object at CALL TIME so tests monkeypatching
``rugbrain.endpoints._analyze_address_findings`` keep working.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from rugbrain import endpoints as _ep
from rugbrain.knowledge_boundary import (
    CollectionNotPublic,
    require_public_collection,
    sanitize_public_result,
)

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["rag-optimization"])


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
    from rugbrain.embeddings import get_query_embedding
    from rugbrain.qdrant_store import QdrantVectorStore, build_search_filters

    if not q:
        return {"results": [], "query": q, "collection": collection, "total": 0}

    try:
        collection = require_public_collection(collection)
    except CollectionNotPublic as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "collection_not_available"},
        ) from exc

    # Rate limit /search by client IP (not spoofable via headers). Each
    # search performs an embedding + Qdrant query (+ optional rerank), so an
    # unthrottled caller could burn significant compute.
    hour_key = datetime.now(UTC).strftime("%Y%m%d%H")
    client_ip = request.client.host if request.client else "unknown"
    allowed, ttl = await _ep._check_rate_limit(client_ip, "search", hour_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "tier": "search",
                "limit": _ep.RATE_LIMITS["search"],
                "reset_in_seconds": ttl,
            },
            headers={"X-RateLimit-Reset": str(ttl)},
        )

    try:
        embedding = await get_query_embedding(q)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={"error": "embedding_unavailable", "message": str(e)[:200]},
        ) from e

    filters = build_search_filters(
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

    store = QdrantVectorStore()
    candidate_limit = min(max(limit * 4, 20), 100) if rerank else limit
    # Hybrid search: dense + BM25 sparse with native Qdrant RRF when the
    # target collection carries a bm25 sparse vector (post sparse-migration
    # canonical collections), falling back to dense + keyword re-rank for
    # dense-only collections. Keeps /api/v1/search serving hybrid results
    # through canonical collection names after the alias swap.
    results = await store.hybrid_search(
        query_text=q,
        query_embedding=embedding,
        collection=collection,
        limit=candidate_limit,
        min_similarity=0.4,
        filters=filters,
    )

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
    rating_filters_applied = any([factuality_filter, min_source_tier, require_corroboration])
    rating_boosts_applied = False
    try:
        from rugbrain.ratings import apply_rating_boosts, filter_docs_by_ratings

        results = filter_docs_by_ratings(
            results,
            factuality_filter=factuality_filter,
            min_source_tier=min_source_tier,
            require_corroboration=require_corroboration,
        )
        results = apply_rating_boosts(results)
        rating_boosts_applied = any(r.get("rating_boosted") for r in results)
        if rating_boosts_applied:
            # Re-sort on the boosted score field (preserve rerank ordering
            # when reranking ran, otherwise the retrieval score).
            score_field = "combined_score" if reranker_applied else "similarity"
            results.sort(key=lambda r: r.get(score_field, 0.0), reverse=True)

        # Surface quality scores on news results: when a doc's metadata
        # carries a rating payload (written by news_rating at ingestion),
        # expose ``quality_score`` (+ new cross-reference / clickbait /
        # on-chain fields) at the top level so callers can rank/filter
        # news without digging into nested metadata.
        for doc in results:
            meta = doc.get("metadata")
            if isinstance(meta, dict) and isinstance(meta.get("rating"), dict):
                rating_payload = meta["rating"]
                if isinstance(rating_payload.get("quality_score"), (int, float)):
                    doc["quality_score"] = int(rating_payload["quality_score"])
                if "cross_referenced" in rating_payload:
                    doc["cross_referenced"] = rating_payload["cross_referenced"]
                if "clickbait_score" in rating_payload:
                    doc["clickbait_score"] = rating_payload["clickbait_score"]
                if "on_chain_verified" in rating_payload:
                    doc["on_chain_verified"] = rating_payload["on_chain_verified"]
    except ImportError:
        logger.debug("rugbrain.ratings not available — skipping rating filters")
    except Exception as e:
        logger.debug("rating filter/boost failed (non-critical): %s", e)

    # ── Chain-data parquet fallback ──
    # When the caller opts in (chain_data=true) and either (a) vector search
    # returned nothing or (b) the query is clearly an on-chain data question
    # ("recent ETH transfers", "who deployed tokens on Base yesterday"), fall
    # back to the indexer's B2 chain-data parquet via the shared DuckDB path.
    # Tagged with source=chain-data-parquet so consumers can distinguish.
    chain_data_applied = False
    chain_data_intent = False
    chain_data_error: str | None = None
    if chain_data:
        try:
            from rugbrain.chain_parquet import (
                build_intent_sql,
                detect_chain_data_intent,
                extract_chain_from_query,
                extract_date_from_query,
                run_chain_query,
            )

            chain_data_intent = detect_chain_data_intent(q)
            if not results or chain_data_intent:
                chain = extract_chain_from_query(q)
                date = extract_date_from_query(q)
                sql = build_intent_sql(q, chain=chain, date=date, limit=limit)
                chain_result = run_chain_query(sql, chain=chain, date=date, limit=limit)
                if chain_result.get("row_count", 0):
                    chain_rows = []
                    cols = chain_result["columns"]
                    for row in chain_result["rows"]:
                        rec = dict(zip(cols, row, strict=False))
                        rec["source"] = "chain-data-parquet"
                        rec["data_source"] = "b2:chain-data/parquet"
                        rec["chain_data"] = True
                        chain_rows.append(rec)
                    results = list(results) + chain_rows
                    chain_data_applied = True
        except Exception as e:
            # Never break vector search on a fallback failure.
            chain_data_error = str(e)[:200]
            logger.debug("chain_data_fallback failed (non-critical): %s", e)

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
    reflection = None
    try:
        from rugbrain.pre_generation_reflection import pre_generation_reflection

        reflection = await pre_generation_reflection(q, results)
    except Exception as e:
        logger.debug("reflection_failed (non-critical): %s", e)

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


# ── Multi-collection search ─────────────────────────────────────────────
_RRF_K = 60


class MultiSearchRequest(BaseModel):
    query: str = ""
    collections: list[str] = ["threat_intel_v2"]
    limit: int = 10
    limit_per_collection: int = 20
    rerank: bool = False


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
    self-hosted Qwen3-Reranker-4B cross-encoder before truncating to ``limit``.

    Example::

        curl -X POST "http://localhost:8012/api/v1/search/multi" \\
          -H "Content-Type: application/json" \\
          -H "X-API-Key: ..." \\
          -d '{"query":"flash loan attack","collections":["threat_intel_v2","scam_patterns"],"rerank":true}'
    """
    from rugbrain.embeddings import get_query_embedding
    from rugbrain.qdrant_store import QdrantVectorStore

    q = body.query
    cols = body.collections
    lim = body.limit
    per_col = body.limit_per_collection
    do_rerank = body.rerank

    if not q:
        return {"results": [], "query": q, "collections": cols, "total": 0}

    try:
        cols = [require_public_collection(col) for col in cols]
    except CollectionNotPublic as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "collection_not_available"},
        ) from exc

    # Rate limit by client IP
    hour_key = datetime.now(UTC).strftime("%Y%m%d%H")
    client_ip = request.client.host if request.client else "unknown"
    allowed, ttl = await _ep._check_rate_limit(client_ip, "search", hour_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "tier": "search",
                "limit": _ep.RATE_LIMITS["search"],
                "reset_in_seconds": ttl,
            },
            headers={"X-RateLimit-Reset": str(ttl)},
        )

    try:
        embedding = await get_query_embedding(q)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={"error": "embedding_unavailable", "message": str(e)[:200]},
        ) from e

    # Search each collection in parallel
    store = QdrantVectorStore()
    tasks = [
        store.hybrid_search(
            query_text=q,
            query_embedding=embedding,
            collection=col,
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


# ── Scam check (separate router: /api/v1/rag/v2) ─────────────────────────


scam_check_router: APIRouter = APIRouter(prefix="/api/v1/rag/v2", tags=["scam-check"])


@scam_check_router.get("/scam-check")
async def scam_check(
    request: Request,
    address: str,
    chain: str = "ethereum",
    limit: int = Query(default=10, ge=1, le=100),
    tier: str = Query(default="free", pattern="^(free|pro)$"),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    hour_key = now.strftime("%Y%m%d%H")
    # IP-based identifier only: X-Device-Id is client-controlled and spoofable,
    # so trusting it would let an attacker rotate headers to bypass the limit.
    client_ip = request.client.host if request.client else "unknown"
    identifier = client_ip

    allowed, ttl = await _ep._check_rate_limit(identifier, tier, hour_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "tier": tier,
                "limit": _ep.RATE_LIMITS.get(tier, _ep.RATE_LIMITS["free"]),
                "reset_in_seconds": ttl,
            },
            headers={"X-RateLimit-Reset": str(ttl)},
        )

    from rugbrain.search_service import three_pillar_search

    query = f"scam check address {address} on {chain}"

    results_data = await three_pillar_search(
        query=query,
        # Search the collections where known-scam / scam-pattern data is
        # actually stored (known_scams + scam_patterns), plus the migrated
        # threat_intel_v2 collection and community reports.
        collections=["known_scams", "scam_patterns", "threat_intel_v2", "rmi_community"],
        limit=limit,
        min_similarity=0.4,
        entity_boost=2.0,
        use_mmr=False,
        use_kg=False,
        use_parent_child=False,
        use_reranker=False,
    )

    matches = []
    max_score = 0.0
    risk_signals = 0

    for r in results_data.get("results", []):
        sim = r.get("similarity", 0)
        sev = r.get("severity", "")
        matches.append(
            sanitize_public_result(
                {
                    "id": r.get("id", ""),
                    "content": (r.get("content", "") or "")[:300],
                    "similarity": round(sim, 4),
                    "severity": sev,
                    "collection": r.get("metadata", {}).get("collection", r.get("collection", "")),
                    "source": r.get("source", ""),
                }
            )
        )
        max_score = max(max_score, sim)
        if sev in ("high", "critical"):
            risk_signals += 1

    if not matches:
        scam_risk = "unknown"
        confidence = 0
    elif max_score > 0.8 or risk_signals >= 2:
        scam_risk = "high"
        confidence = min(100, int(max_score * 100))
    elif max_score > 0.6 or risk_signals >= 1:
        scam_risk = "medium"
        confidence = min(85, int(max_score * 100))
    elif max_score > 0.4:
        scam_risk = "low"
        confidence = min(60, int(max_score * 100))
    else:
        scam_risk = "unknown"
        confidence = min(40, int(max_score * 100))

    return {
        "address": address,
        "chain": chain,
        "scam_risk": scam_risk,
        "matches": matches,
        "confidence": confidence,
        "total_matches": len(matches),
    }


# ── Crypto Code Mode (ContextCrypto collections) ──────────────────────


@router.get("/search/crypto-code")
async def search_crypto_code_endpoint(
    q: str = Query(..., min_length=2, description="public crypto-code question"),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    """Search reviewed public/open-source crypto code and exploit knowledge.

    This endpoint never reads ContextCrypto's internal collections. It serves
    only the redacted ``public_crypto_patterns`` projection and refuses
    requests to weaponize or execute attacks.
    """
    from rugbrain.public_crypto_code import public_code_request_allowed

    if not public_code_request_allowed(q):
        return {
            "query": q,
            "routed": False,
            "blocked": True,
            "reason": "unsafe_request",
            "results": [],
            "total": 0,
            "message": "I can help with defensive analysis, public advisories, and authorized code review.",
        }

    from rugbrain.public_crypto_code import search_public_crypto_code

    return await search_public_crypto_code(q, top_k=limit)
