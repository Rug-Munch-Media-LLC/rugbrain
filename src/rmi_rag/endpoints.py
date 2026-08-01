"""
RAG optimization endpoints - included via APIRouter for clean git history.
Confidence scoring, Solidity chunking, deployer reputation, cross-chain,
multi-modal, investigation narratives, graph RAG, streaming, email.
"""

import logging
import os
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)

RATE_LIMITS = {
    "free": 5,
    "pro": 100,
}


async def _check_rate_limit(identifier: str, tier: str, hour_key: str) -> tuple[bool, int]:
    """Check the per-identifier hourly rate limit.

    Fails OPEN when Redis is unavailable: rate limiting is a best-effort
    guard, not a hard dependency — the route should still serve results
    when the limiter backend is down.
    """
    from rmi_rag.search_service import _get_redis

    try:
        rd = await _get_redis()
        limit = RATE_LIMITS.get(tier, RATE_LIMITS["free"])
        key = f"rag:ratelimit:{identifier}:{hour_key}"
        current = await rd.incr(key)
        if current == 1:
            now = datetime.now(UTC)
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            ttl = int((next_hour - now).total_seconds())
            await rd.expire(key, ttl)
        else:
            ttl = await rd.ttl(key)
            if ttl < 0:
                ttl = 3600
        if current > limit:
            return False, ttl
        return True, ttl
    except Exception as exc:
        logger.warning("rate_limit_unavailable: failing open — %s", exc)
        return True, 3600


router = APIRouter(prefix="/api/v1", tags=["rag-optimization"])


@router.post("/rag/confidence")
async def rag_confidence_score(request: Request, data: dict):
    from app.confidence import score_confidence  # external dependency

    return score_confidence(
        data.get("results", []),
        query=data.get("query", ""),
        entity_matches=data.get("entity_matches", []),
    )


@router.post("/rag/chunk-solidity")
async def rag_chunk_solidity(request: Request, data: dict):
    source = data.get("source", "")
    if not source:
        return {"error": "source is required"}
    from rmi_rag.solidity_chunker import chunk_solidity_ast

    chunks = chunk_solidity_ast(source)
    return {"chunks": chunks, "count": len(chunks)}


@router.post("/scanner/deployer-risk")
async def scanner_deployer_risk(request: Request, data: dict):
    from app.deployer_reputation import score_deployer_risk  # external dependency

    return score_deployer_risk(
        address=data.get("address", ""),
        deployment_count=data.get("deployment_count", 0),
        mixer_funded=data.get("mixer_funded", False),
        source_verified=data.get("source_verified", False),
        code_similarity=data.get("code_similarity", 0.0),
        rapid_deploy=data.get("rapid_deploy", False),
        funded_by_scammer=data.get("funded_by_scammer", False),
    )


@router.post("/rag/cross-chain")
async def rag_cross_chain(request: Request, data: dict):
    from app.cross_chain import resolve_cross_chain_identity  # external dependency

    return resolve_cross_chain_identity(
        address=data.get("address", ""),
        chain=data.get("chain", "ethereum"),
        funding_sources=data.get("funding_sources", []),
        label_hints=data.get("label_hints", []),
    )


@router.post("/rag/image-analysis")
async def rag_image_analysis(request: Request, data: dict):
    from rmi_rag.multimodal import describe_image

    return {
        "task": data.get("task", "describe"),
        "description": await describe_image(
            image_url=data.get("image_url", ""), task=data.get("task", "describe")
        ),
    }


@router.post("/rag/logo-check")
async def rag_logo_check(request: Request, data: dict):
    url = data.get("image_url", "")
    if not url:
        return {"error": "image_url is required"}
    r = await httpx.AsyncClient(timeout=15).get(url)
    from rmi_rag.multimodal import compute_image_hash, get_logo_db

    h = compute_image_hash(r.content)
    return {**get_logo_db().check_theft(h), "computed_hash": h}


@router.post("/rag/investigate")
async def rag_investigate_endpoint(request: Request, data: dict):
    # Accept either ``query`` (natural language) or ``address`` (token/wallet).
    q = data.get("query", "") or data.get("address", "")
    if not q:
        return {"error": "query is required"}
    from app.investigation_narratives import investigate  # external dependency

    return await investigate(
        query=q, investigation_type=data.get("type", "auto"), max_hops=data.get("max_hops", 5)
    )


@router.get("/rag/graph-communities")
async def rag_graph_communities(request: Request):
    from rmi_rag.graph_rag import graph_rag_search

    return await graph_rag_search(query="")


@router.post("/rag/watch")
async def rag_create_watch(request: Request, data: dict):
    from rmi_rag.streaming import create_watch

    return await create_watch(
        token_address=data.get("token_address", ""), chain=data.get("chain", "ethereum")
    )


@router.get("/rag/watches")
async def rag_list_watches(request: Request):
    from rmi_rag.streaming import list_active_watches

    return await list_active_watches()


@router.get("/rag/journalism/research")
async def journalism_research(
    request: Request,
    topic: str = Query(
        default="", description="Research topic — e.g. 'cross-chain bridge hacks 2024'"
    ),
    limit: int = Query(default=15, ge=1, le=50, description="Max docs per collection"),
) -> dict:
    """
    Deep-dive research for crypto journalism.

    Queries all three RAG collections (``threat_intel``, ``rmi_agent_memory``,
    ``rmi_community``) and returns a structured research brief with:

    - **Timeline**: Chronological events extracted from threat intel
    - **Key Entities**: Threat actors, protocols, vulnerability patterns
    - **Related Scams**: Matching scam/hack records with severity classification
    - **Community Insights**: Best practices and contextual knowledge
    - **Cross-Collection Connections**: Entities linked across threat and memory data
    - **Synthesis**: Markdown summary with quality metrics

    Designed for:
    - **Journalists**: Research briefs for articles and investigations
    - **Bots**: Structured data for automated content generation
    - **Scanners**: Context enrichment during threat analysis
    - **Historical analysis**: Trend identification across events

    Example::

        curl "http://localhost:8012/api/v1/rag/journalism/research?topic=Solana+bridge+security"
    """
    from rmi_rag.journalism import research_topic

    if not topic:
        return {
            "topic": "",
            "error": "topic query parameter is required",
            "status": "error",
            "research_timestamp": datetime.now(UTC).isoformat(),
        }

    try:
        brief = await research_topic(topic, max_docs=limit)
        brief["status"] = (
            "ok"
            if brief.get("total_docs_retrieved", {}).get("threat_intel", 0) > 0
            else "low_relevance"
        )
        return brief
    except Exception as e:
        logger.exception("journalism_research_failed topic=%s", topic[:60])
        return {
            "topic": topic,
            "error": str(e)[:300],
            "status": "error",
            "research_timestamp": datetime.now(UTC).isoformat(),
        }


@router.get("/email/accounts")
async def email_accounts(request: Request):
    """List configured mail accounts.

    The account list comes from the ``RMI_EMAIL_ACCOUNTS`` env var — a
    comma-separated list of ``id|email|type`` entries — so no personal
    addresses are hardcoded in the repo. Returns an empty list when
    unconfigured.
    """
    accounts: list[dict[str, str]] = []
    for entry in os.getenv("RMI_EMAIL_ACCOUNTS", "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) == 2:
            account_id, email = parts
            account_type = "gmail"
        elif len(parts) == 3:
            account_id, email, account_type = parts
        else:
            logger.warning("email_accounts: skipping malformed entry %r", entry[:40])
            continue
        accounts.append({"id": account_id, "email": email, "type": account_type})

    return {
        "accounts": accounts,
        "webmail_url": os.getenv("RMI_WEBMAIL_URL", "https://webmail.rugmunch.io/"),
    }


@router.get("/search")
async def search_threat_intel(
    request: Request,
    q: str = Query(
        default="", description="Search query — token address, name, or scam description"
    ),
    limit: int = Query(default=10, ge=1, le=100),
    collection: str = Query(default="threat_intel", description="Qdrant collection to search"),
    rerank: bool = Query(
        default=False,
        description="Rerank top-20 candidates with the bge-reranker-v2-m3 cross-encoder",
    ),
) -> dict:
    """
    Semantic search across scanner findings ingested into Qdrant.

    Queries the ``threat_intel`` collection (populated in real-time by the
    scanner subscriber) for tokens that match the query by semantic similarity.

    When ``rerank=true``, the top-20 candidates from vector search are scored
    by the bge-reranker-v2-m3 cross-encoder and re-sorted before truncating
    to ``limit``. If the reranker backend is unavailable, results fall back
    to the original vector ranking (search never breaks).

    Example::

        curl "http://localhost:8012/api/v1/search?q=0xdead...&limit=5&rerank=true"
    """
    from rmi_rag.embeddings import get_embedding
    from rmi_rag.qdrant_store import QdrantVectorStore

    if not q:
        return {"results": [], "query": q, "collection": collection, "total": 0}

    try:
        embedding = await get_embedding(q)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail={"error": "embedding_unavailable", "message": str(e)[:200]},
        ) from e

    store = QdrantVectorStore()
    candidate_limit = min(max(limit * 4, 20), 100) if rerank else limit
    results = await store.search(
        query_embedding=embedding,
        collection=collection,
        limit=candidate_limit,
        min_similarity=0.4,
    )

    reranker_method = None
    reranker_applied = False
    if rerank and results:
        try:
            from rmi_rag.reranker import get_reranker

            reranker = await get_reranker()
            reranked = await reranker.rerank(q, results, top_k=limit)
            if reranked and reranked[0].get("rerank_method", "none") != "none":
                results = reranked
                reranker_applied = True
                reranker_method = reranked[0].get("rerank_method")
        except Exception as e:
            logger.debug("search rerank failed (non-critical): %s", e)

    return {
        "results": results,
        "query": q,
        "collection": collection,
        "total": len(results),
        "reranker_applied": reranker_applied,
        "reranker_method": reranker_method,
    }


scam_check_router = APIRouter(prefix="/api/v1/rag/v2", tags=["scam-check"])


@scam_check_router.get("/scam-check")
async def scam_check(
    request: Request,
    address: str,
    chain: str = "ethereum",
    limit: int = Query(default=10, ge=1, le=100),
    tier: str = Query(default="free", pattern="^(free|pro)$"),
) -> dict:
    now = datetime.now(UTC)
    hour_key = now.strftime("%Y%m%d%H")
    device_id = request.headers.get("X-Device-Id", "")
    client_ip = request.client.host if request.client else "unknown"
    identifier = device_id if device_id else client_ip

    allowed, ttl = await _check_rate_limit(identifier, tier, hour_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "tier": tier,
                "limit": RATE_LIMITS.get(tier, RATE_LIMITS["free"]),
                "reset_in_seconds": ttl,
            },
            headers={"X-RateLimit-Reset": str(ttl)},
        )

    from rmi_rag.search_service import three_pillar_search

    query = f"scam check address {address} on {chain}"

    results_data = await three_pillar_search(
        query=query,
        collections=["threat_intel", "rmi_community"],
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
            {
                "id": r.get("id", ""),
                "content": (r.get("content", "") or "")[:300],
                "similarity": round(sim, 4),
                "severity": sev,
                "collection": r.get("metadata", {}).get("collection", r.get("collection", "")),
                "source": r.get("source", ""),
            }
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
