"""
RAG optimization endpoints - included via APIRouter for clean git history.
Confidence scoring, Solidity chunking, deployer reputation, cross-chain,
multi-modal, investigation narratives, graph RAG, streaming, email.
"""

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

RATE_LIMITS = {
    "free": 5,
    "pro": 100,
    # /api/v1/search is called by the backend scanner per token over a shared
    # egress IP, so the per-IP limit must be generous but still bound abuse
    # (each search costs an embedding + Qdrant query + optional rerank).
    "search": int(os.getenv("RAG_SEARCH_RATE_LIMIT", "3600")),
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
        key = f"rag:ratelimit:{tier}:{identifier}:{hour_key}"
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


_TOKEN_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def _is_token_address(q: str) -> bool:
    """True when *q* is a bare token/contract address (0x + 40 hex)."""
    return bool(_TOKEN_ADDRESS_RE.fullmatch((q or "").strip()))


async def _analyze_address_findings(address: str, chain: str, limit: int) -> dict[str, Any]:
    """Run the historical pattern-matching + cross-reference pipeline.

    Fail-open: any failure returns an empty finding block so the enclosing
    search/analyze endpoint still serves results.
    """
    try:
        from rmi_rag.cross_reference import cross_reference_token
        from rmi_rag.pattern_matching import match_token_patterns, score_scam_risk

        # Build a minimal token profile for matching (chain + address context).
        token_data = {"address": address, "chain": chain, "token_address": address}
        matching = await match_token_patterns(token_data)
        matched = matching.get("matched_patterns", [])
        signals = matching.get("risk_signals", [])
        verdict = score_scam_risk(matched, signals)

        cross = await cross_reference_token(
            address,
            chain=chain,
            limit=limit,
            matched_patterns=matched,
        )

        # Contract-security assessment (contract-knowledge expansion): fetch
        # verified source (Sourcify/Etherscan), run static heuristics, and
        # compare against the solidity_patterns / contract_security /
        # audit_findings knowledge collections. Fail-open: analyze returns a
        # benign dict when no source or knowledge backend is available.
        from rmi_rag.contract_analysis import analyze_contract_security

        contract_security = await analyze_contract_security(address, chain=chain, limit=limit)

        # Fearful market → higher risk on new tokens (search/analyze feed).
        fear_value: int | None = None
        try:
            from rmi_rag.fear_index import adjust_risk_for_market, current_fear_value

            fear_value = await current_fear_value()
            verdict = adjust_risk_for_market(verdict, fear_value)
        except Exception as e:
            logger.debug("fear_index_adjust_failed (non-critical): %s", e)

        return {
            "address": address,
            "chain": chain,
            "verdict": verdict,
            "fear_index": fear_value,
            "matched_patterns": matched[:limit],
            "risk_signals": signals[:25],
            "cross_reference": cross,
            "contract_security": contract_security,
        }
    except Exception as e:
        logger.warning("analyze_address_findings failed (non-critical): %s", e)
        return {
            "address": address,
            "chain": chain,
            "verdict": {"risk": "unknown", "confidence": 0.0, "top_pattern": ""},
            "fear_index": None,
            "matched_patterns": [],
            "risk_signals": [],
            "cross_reference": {
                "address": address,
                "chain": chain,
                "backend": "none",
                "similar_tokens": [],
                "narrative": "",
            },
            "contract_security": {
                "address": address,
                "chain": chain,
                "source_fetched": False,
                "backend": "none",
                "assessment": "unknown",
                "summary": "",
                "error": str(e)[:200],
            },
            "error": str(e)[:200],
        }


@router.get("/analyze/{address}")
async def analyze_token_address(
    request: Request,
    address: str,
    chain: str = Query(default="ethereum", description="Chain of the token/contract"),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    """Run the full historical scam-analysis pipeline on a token address.

    Combines the pattern-matching engine (bytecode + deployer behavior +
    token economics against known scam patterns) with historical
    cross-reference (similar tokens from lite-indexer LanceDB / Qdrant
    threat-intel) and returns a structured verdict::

        {
          "address", "chain",
          "verdict": {"risk": "high|medium|low|unknown", "confidence", "top_pattern"},
          "matched_patterns": [{"name", "confidence", "reference_incident", "technique"}],
          "risk_signals": ["..."],
          "cross_reference": {"backend", "similar_tokens", "narrative"},
        }

    Rate-limited per client IP (free tier = 5/hour).
    """
    now = datetime.now(UTC)
    hour_key = now.strftime("%Y%m%d%H")
    client_ip = request.client.host if request.client else "unknown"
    allowed, ttl = await _check_rate_limit(client_ip, "free", hour_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "tier": "free",
                "limit": RATE_LIMITS["free"],
                "reset_in_seconds": ttl,
            },
            headers={"X-RateLimit-Reset": str(ttl)},
        )

    if not _is_token_address(address):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_address",
                "message": "address must be a 0x-prefixed 40-hex token/contract address",
            },
        )

    findings = await _analyze_address_findings(address, chain, limit)
    return findings


@router.post("/rag/query", response_model=None)
async def rag_query_snapshots(
    request: Request, data: dict[str, Any]
) -> dict[str, Any] | JSONResponse:
    """Run read-only SQL over RAG permanence parquet snapshots via DuckDB.

    Body: ``{"query": "SELECT ...", "date": "2026-08-01"?, "limit": N?}``.
    A read-only ``snapshots`` view exposes every parquet snapshot under
    ``s3://{B2_BUCKET_NAME}/snapshots/`` (filtered by ``date`` when
    given), so callers can ask questions like:

        SELECT content, metadata FROM snapshots
        WHERE json_extract_string(metadata, '$.severity') = 'critical';

    Queries are validated to be single, read-only statements (no DDL,
    DML, extension loading, or external file reads). Credentials come
    from the process environment (B2_APPLICATION_KEY_ID/KEY,
    B2_BUCKET_NAME, B2_S3_ENDPOINT) and are never exposed.
    """
    query = data.get("query", "")
    if not query or not str(query).strip():
        return {"error": "query is required", "code": "query.missing"}
    date = data.get("date")
    try:
        limit = max(1, min(int(data.get("limit", 1000)), 5000))
    except (TypeError, ValueError):
        limit = 1000

    from rmi_rag.parquet_analytics import run_snapshot_query, validate_read_only_sql

    try:
        validate_read_only_sql(str(query))
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": str(exc), "code": "query.invalid"},
        )

    try:
        result = run_snapshot_query(str(query), date=date, limit=limit)
    except Exception as exc:
        logger.warning("rag_query_failed err=%s", exc)
        return JSONResponse(
            status_code=502,
            content={
                "error": f"snapshot query failed: {exc}",
                "code": "query.execution_failed",
            },
        )
    return result


@router.post("/rag/chain/query", response_model=None)
async def rag_chain_query(request: Request, data: dict[str, Any]) -> dict[str, Any] | JSONResponse:
    """Run read-only SQL over chain-indexer parquet snapshots via DuckDB.

    Body: ``{"query": "SELECT ...", "chain": "ethereum"?, "date":
    "2026-08-03"?, "hour": "00"?, "limit": N?}``.

    This is the *second* B2 parquet data source: the chain-indexer's
    hourly on-chain snapshots (transfers + DEX swaps) at
    ``b2-crypt:chain-data/parquet/{chain}/{YYYY}/{MM}/{DD}/{HH}/``.
    Read-only ``chain_events`` (transfers) and ``chain_dex`` (DEX swaps)
    views are exposed over the requested chain/date/hour prefix, so
    callers can ask questions like:

        SELECT chain, from_address, to_address, token_address, value
        FROM chain_events WHERE chain = 'gnosis'
        ORDER BY block_timestamp DESC LIMIT 10;

    The prefix is downloaded through ``rclone`` (the ``b2-crypt`` remote
    is content-encrypted, so DuckDB httpfs cannot read it directly) and
    queried locally. ``chain`` is required when querying B2; ``date`` /
    ``hour`` narrow the download. Credentials come from the rclone
    config / process environment and are never exposed.
    """
    query = data.get("query", "")
    if not query or not str(query).strip():
        return {"error": "query is required", "code": "query.missing"}
    chain = data.get("chain")
    date = data.get("date")
    hour = data.get("hour")
    try:
        limit = max(1, min(int(data.get("limit", 1000)), 5000))
    except (TypeError, ValueError):
        limit = 1000

    from rmi_rag.chain_parquet import run_chain_query
    from rmi_rag.parquet_analytics import validate_read_only_sql

    try:
        validate_read_only_sql(str(query))
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": str(exc), "code": "query.invalid"},
        )

    try:
        result = run_chain_query(
            str(query),
            chain=str(chain) if chain else None,
            date=str(date) if date else None,
            hour=str(hour) if hour else None,
            limit=limit,
        )
    except Exception as exc:
        logger.warning("rag_chain_query_failed err=%s", exc)
        return JSONResponse(
            status_code=502,
            content={
                "error": f"chain query failed: {exc}",
                "code": "query.execution_failed",
            },
        )
    return result


@router.get("/rag/chain/catalog")
async def rag_chain_catalog(request: Request) -> dict[str, Any]:
    """List the chain-data parquet source available to the RAG.

    Returns the configured B2 remote/bucket/prefix, the chain names the
    indexer writes, and an example prefix for reference. This is static
    metadata (no B2 round-trip) so callers know what the
    ``/api/v1/rag/chain/query`` endpoint can reach.
    """
    from rmi_rag.chain_parquet import (
        DEFAULT_CHAIN_BUCKET,
        DEFAULT_CHAIN_PREFIX,
        DEFAULT_CHAIN_REMOTE,
        KNOWN_CHAINS,
    )

    return {
        "data_source": "chain-data-parquet",
        "remote": f"{DEFAULT_CHAIN_REMOTE}:{DEFAULT_CHAIN_BUCKET}",
        "prefix": DEFAULT_CHAIN_PREFIX,
        "layout": "parquet/{chain}/{YYYY}/{MM}/{DD}/{HH}/*.parquet",
        "views": ["chain_events (transfers)", "chain_dex (DEX swaps)"],
        "chains": KNOWN_CHAINS,
        "example_query": (
            "SELECT chain, from_address, to_address, value "
            "FROM chain_events ORDER BY block_timestamp DESC LIMIT 5"
        ),
    }


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


@router.get("/eval/run")
async def eval_run(
    request: Request,
    limit: int = 0,
    skip_answers: bool = False,
) -> dict:
    """Run the RAGAS-style golden-set evaluation against the live search endpoint.

    Executes ``run_evaluation()`` from ``rmi_rag.evaluation``: every query in
    the golden test set is sent through ``/api/v1/search`` and scored for
    context precision/recall. Returns the aggregate pass/fail report plus
    per-query results.

    Queries run concurrently and each is bounded by a 10s timeout, so the
    full run completes in seconds-to-a-minute instead of 30+ minutes.

    Example::

        curl "http://localhost:8012/api/v1/eval/run"
        curl "http://localhost:8012/api/v1/eval/run?limit=3"         # smoke
        curl "http://localhost:8012/api/v1/eval/run?skip_answers=1"  # retrieval-only
    """
    from rmi_rag.evaluation import run_evaluation

    try:
        return await run_evaluation(
            limit=limit or None,
            skip_answers=skip_answers,
        )
    except Exception as exc:
        logger.warning("eval_run_failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"error": "evaluation_failed", "message": str(exc)[:200]},
        ) from exc


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
) -> dict:
    """
    Semantic search across scanner findings ingested into Qdrant.

    Queries the ``threat_intel`` collection (populated in real-time by the
    scanner subscriber) for tokens that match the query by semantic similarity.

    When ``rerank=true``, the top-20 candidates from vector search are scored
    by the bge-reranker-v2-m3 cross-encoder and re-sorted before truncating
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

        curl "http://localhost:8012/api/v1/search?q=reentrancy&collection=threat_intel&filter_severity=critical&filter_chain=ethereum&filter_date_after=2023-01-01&limit=5&factuality_filter=verified&require_corroboration=1"
    """
    from rmi_rag.embeddings import get_embedding
    from rmi_rag.qdrant_store import QdrantVectorStore, build_search_filters

    if not q:
        return {"results": [], "query": q, "collection": collection, "total": 0}

    # Rate limit /search by client IP (not spoofable via headers). Each
    # search performs an embedding + Qdrant query (+ optional rerank), so an
    # unthrottled caller could burn significant compute.
    hour_key = datetime.now(UTC).strftime("%Y%m%d%H")
    client_ip = request.client.host if request.client else "unknown"
    allowed, ttl = await _check_rate_limit(client_ip, "search", hour_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "tier": "search",
                "limit": RATE_LIMITS["search"],
                "reset_in_seconds": ttl,
            },
            headers={"X-RateLimit-Reset": str(ttl)},
        )

    try:
        embedding = await get_embedding(q)
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

    # ── Ground News-style rating filters + boosts ──
    # Optional post-search filters drop articles whose rating conflicts with
    # the requested quality bar. Rating boosts then reweight the survivors so
    # verified/corroborated news ranks above unrated or rumor content.
    rating_filters_applied = any([factuality_filter, min_source_tier, require_corroboration])
    rating_boosts_applied = False
    try:
        from rmi_rag.ratings import apply_rating_boosts, filter_docs_by_ratings

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
        logger.debug("rmi_rag.ratings not available — skipping rating filters")
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
            from rmi_rag.chain_parquet import (
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
    if _is_token_address(q):
        chain_hint = filter_chain or "ethereum"
        pattern_matches = await _analyze_address_findings(
            q.strip().lower(), chain=chain_hint, limit=min(limit, 25)
        )

    return {
        "results": results,
        "query": q,
        "collection": collection,
        "total": len(results),
        "filters_applied": filters is not None,
        "reranker_applied": reranker_applied,
        "reranker_method": reranker_method,
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
    # IP-based identifier only: X-Device-Id is client-controlled and spoofable,
    # so trusting it would let an attacker rotate headers to bypass the limit.
    client_ip = request.client.host if request.client else "unknown"
    identifier = client_ip

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


# ═══════════════════════════════════════════════════════════════════════
# News rating + crypto fear index (RugBrain news ranking)
# ═══════════════════════════════════════════════════════════════════════

NEWS_TOP_MAX_SCROLL_PAGES = 10


@router.get("/news/rate")
async def news_rate_article(
    request: Request,
    url: str = Query(
        default=...,
        description=(
            "Article URL — used for source-reputation scoring. Never fetched "
            "server-side (SSRF-safe); rate only the provided metadata."
        ),
    ),
    source: str | None = Query(default=None, description="Source name (optional, e.g. 'CoinDesk')"),
    title: str | None = Query(default=None, description="Article title"),
    content: str | None = Query(
        default=None, description="Article body text — recommended for a full rating"
    ),
    published_at: str | None = Query(
        default=None, description="ISO-8601 publish timestamp (e.g. 2026-08-04T09:00:00Z)"
    ),
    corroborating_sources: int | None = Query(
        default=None, ge=0, le=100, description="Independent sources reporting the same event"
    ),
    source_reputation_override: int | None = Query(
        default=None, ge=0, le=100, description="Explicit 0-100 source reputation score"
    ),
    aggregated: bool = Query(
        default=False, description="True when the article aggregates multiple sources"
    ),
) -> dict[str, Any]:
    """Rate a news article on seven quality axes (Ground News-style).

    Computes a 0-100 score per axis (source credibility, factual density,
    timeliness, bias, corroboration, technical depth, source diversity)
    plus a weighted 0-100 composite and a tier. The URL is used only for
    source reputation lookup — it is **never fetched server-side** (SSRF
    safety). Pass the article body via ``content`` for a full rating::

        curl "http://localhost:8012/api/v1/news/rate?url=https://coindesk.com/...&content=The%20protocol%20lost%20%2412m..."
    """
    from rmi_rag.news_rating import rate_article

    rating = rate_article(
        {
            "url": url,
            "source": source or "",
            "title": title or "",
            "content": content or "",
            "published_at": published_at,
            "corroborating_sources": corroborating_sources,
            "source_reputation_override": source_reputation_override,
            "aggregated": aggregated,
        }
    )
    return {
        "url": rating.url,
        "source": rating.source,
        "axes": rating.axes,
        "composite": rating.composite,
        "tier": rating.tier,
        "factuality": rating.factuality,
        "source_reputation": rating.source_reputation,
        "corroborating_sources": rating.corroborating_sources,
        "on_chain_evidence": rating.on_chain_evidence,
        "signals": rating.signals,
        "rated_at": datetime.now(UTC).isoformat(),
        "note": (
            "rating computed from provided metadata only; the URL is never fetched server-side"
        ),
    }


@router.get("/news/top")
async def news_top_articles(
    request: Request,
    category: str | None = Query(
        default=None,
        description="Filter by category (e.g. security, defi, ethereum, solana)",
    ),
    min_quality: int = Query(
        default=70, ge=0, le=100, description="Minimum composite quality 0-100"
    ),
    limit: int = Query(default=20, ge=1, le=100, description="Max articles to return"),
) -> dict[str, Any]:
    """Return the best-rated articles from the Qdrant `news` collection.

    Scrolls the collection (payload has no vector score), keeps only points
    whose stored rating composite is >= ``min_quality`` (optionally filtered
    by ``category``), and returns them sorted by quality descending. Legacy
    unrated points are excluded — the rating engine runs at ingestion time.
    Fails open to an empty list when Qdrant is unreachable.
    """
    from rmi_rag.news_rating import rate_article_from_rating_dict
    from rmi_rag.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore()
    collected: list[dict[str, Any]] = []
    try:
        offset: str | None = None
        page = 0
        while page < NEWS_TOP_MAX_SCROLL_PAGES and len(collected) < limit * 4:
            results, offset = await store.scroll("news", limit=100, offset=offset)
            if not results:
                break
            collected.extend(results)
            page += 1
            if not offset:
                break
    except Exception as exc:
        logger.warning("news_top_scroll_failed: %s", exc)
        return {
            "articles": [],
            "total": 0,
            "category": category,
            "min_quality": min_quality,
            "collection": "news",
            "error": str(exc)[:200],
        }

    articles: list[dict[str, Any]] = []
    for result in collected:
        metadata = result.get("metadata") or {}
        if category and str(metadata.get("category", "")).lower() != str(category).lower():
            continue
        rating = metadata.get("rating")
        if not isinstance(rating, dict):
            continue  # unrated legacy points carry no quality signal
        payload = rate_article_from_rating_dict(rating)
        quality = int(payload.get("quality_score", 0))
        if quality < min_quality:
            continue
        articles.append(
            {
                "id": result.get("id"),
                "title": metadata.get("title", ""),
                "source": metadata.get("source", result.get("source", "")),
                "url": metadata.get("url", ""),
                "published_at": metadata.get("published_at", ""),
                "category": metadata.get("category", ""),
                "quality": quality,
                "tier": payload.get("tier", "fair"),
                "factuality": payload.get("factuality", "reported"),
                "axes": payload.get("axes", {}),
                "rating": payload,
            }
        )

    articles.sort(key=lambda article: article["quality"], reverse=True)
    return {
        "articles": articles[:limit],
        "total": len(articles),
        "category": category,
        "min_quality": min_quality,
        "collection": "news",
    }


@router.get("/fear-index")
async def fear_index_current(
    request: Request,
    window: str = Query(
        default="24h",
        pattern="^(1h|6h|24h|7d)$",
        description="Sample window for the returned trend (1h|6h|24h|7d)",
    ),
) -> dict[str, Any]:
    """Current crypto panic/fear index (0 = extreme fear, 100 = extreme greed).

    Blends Alternative.me Fear & Greed, SearXNG social sentiment, on-chain
    exchange/whale/liquidation signals and aggregated news sentiment. The
    snapshot is polled every 5 minutes, cached in Redis, and persisted to
    ClickHouse. Fails open to a neutral 50 when no provider is reachable::

        curl "http://localhost:8012/api/v1/fear-index?window=24h"
    """
    from rmi_rag.fear_index import get_fear_index

    try:
        return await get_fear_index(window=window)
    except Exception as exc:
        logger.warning("fear_index_endpoint_failed: %s", exc)
        return {
            "value": 50,
            "classification": "neutral",
            "components": {},
            "timestamp": datetime.now(UTC).isoformat(),
            "window": window,
            "samples": [],
            "source": "error",
            "error": str(exc)[:200],
        }


@router.get("/fear-index/history")
async def fear_index_history_endpoint(
    request: Request,
    days: int = Query(default=30, ge=1, le=365, description="Number of days of history"),
) -> dict[str, Any]:
    """Historical fear-index chart data (ClickHouse-first, memory fallback).

    Returns ``{"days", "points": [{"ts", "value", "classification"}],
    "source"}`` suitable for charting::

        curl "http://localhost:8012/api/v1/fear-index/history?days=30"
    """
    from rmi_rag.fear_index import fear_index_history

    try:
        return await fear_index_history(days=days)
    except Exception as exc:
        logger.warning("fear_index_history_failed: %s", exc)
        return {
            "days": days,
            "points": [],
            "source": "error",
            "error": str(exc)[:200],
        }
