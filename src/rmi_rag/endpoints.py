"""
RAG optimization endpoints - included via APIRouter for clean git history.
Confidence scoring, Solidity chunking, deployer reputation, cross-chain,
multi-modal, investigation narratives, graph RAG, streaming, email.
"""

import logging
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger(__name__)

RATE_LIMITS = {
    "free": 5,
    "pro": 100,
}


async def _check_rate_limit(
    identifier: str, tier: str, hour_key: str
) -> tuple[bool, int]:
    from rmi_rag.search_service import _get_redis

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
        "description": await describe_image(image_url=data.get("image_url", ""), task=data.get("task", "describe")),
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
    q = data.get("query", "")
    if not q:
        return {"error": "query is required"}
    from app.investigation_narratives import investigate  # external dependency

    return await investigate(query=q, investigation_type=data.get("type", "auto"), max_hops=data.get("max_hops", 5))


@router.get("/rag/graph-communities")
async def rag_graph_communities(request: Request):
    from rmi_rag.graph_rag import graph_rag_search

    return await graph_rag_search(query="")


@router.post("/rag/watch")
async def rag_create_watch(request: Request, data: dict):
    from rmi_rag.streaming import create_watch

    return await create_watch(token_address=data.get("token_address", ""), chain=data.get("chain", "ethereum"))


@router.get("/rag/watches")
async def rag_list_watches(request: Request):
    from rmi_rag.streaming import list_active_watches

    return await list_active_watches()


@router.get("/email/accounts")
async def email_accounts(request: Request):
    return {
        "accounts": [
            {"id": "gmail", "email": "cryptorugmuncher@gmail.com", "type": "gmail"},
            {"id": "rmi", "email": "admin@rugmunch.io", "type": "gmail"},
            {"id": "rmi-local", "email": "admin@rugmunch.io", "type": "local"},
            {"id": "biz", "email": "biz@rugmunch.io", "type": "local"},
        ],
        "webmail_url": "https://webmail.rugmunch.io/",
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
        collections=["known_scams", "scam_patterns"],
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
        matches.append({
            "id": r.get("id", ""),
            "content": (r.get("content", "") or "")[:300],
            "similarity": round(sim, 4),
            "severity": sev,
            "collection": r.get("metadata", {}).get("collection", r.get("collection", "")),
            "source": r.get("source", ""),
        })
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
