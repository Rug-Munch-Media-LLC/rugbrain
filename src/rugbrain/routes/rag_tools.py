"""RAG tool routes: parquet SQL, confidence, chunking, identity, multimodal,
investigation, watches.

Split out of ``rugbrain/endpoints.py`` (degodding phase 3, 2026-08-21).
No URL prefix here — the parent router in ``endpoints.py`` supplies
``/api/v1``.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from rugbrain.knowledge_boundary import sanitize_public_result

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["rag-optimization"])


@router.post("/rag/query", response_model=None)
async def rag_query_snapshots(
    request: Request, data: dict[str, Any]
) -> dict[str, Any] | JSONResponse:
    """Run a bounded, read-only query over public RAG snapshot data.

    The public response contains only query results; storage locations and
    credentials are never returned.
    """
    query = data.get("query", "")
    if not query or not str(query).strip():
        return {"error": "query is required", "code": "query.missing"}
    date = data.get("date")
    try:
        limit = max(1, min(int(data.get("limit", 1000)), 5000))
    except (TypeError, ValueError):
        limit = 1000

    from rugbrain.parquet_analytics import run_snapshot_query, validate_read_only_sql

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
                "error": "snapshot query unavailable",
                "code": "query.execution_failed",
            },
        )
    return cast(dict[str, Any], sanitize_public_result(result))


@router.post("/rag/chain/query", response_model=None)
async def rag_chain_query(request: Request, data: dict[str, Any]) -> dict[str, Any] | JSONResponse:
    """Run a bounded, read-only query over public chain-index data.

    The public response contains only query results; storage locations,
    remotes, and credentials are never returned.
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

    from rugbrain.chain_parquet import run_chain_query
    from rugbrain.parquet_analytics import validate_read_only_sql

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
                "error": "chain query unavailable",
                "code": "query.execution_failed",
            },
        )
    return cast(dict[str, Any], sanitize_public_result(result))


@router.get("/rag/chain/catalog")
async def rag_chain_catalog(request: Request) -> dict[str, Any]:
    """List the public chain-data capabilities without storage details.

    Storage remotes, buckets, prefixes, and provider names are intentionally
    omitted from this public response.
    """
    from rugbrain.chain_parquet import KNOWN_CHAINS

    return {
        "data_source": "chain-data",
        "layout": "chain data partitioned by chain and time",
        "views": ["chain_events (transfers)", "chain_dex (DEX swaps)"],
        "chains": KNOWN_CHAINS,
        "example_query": (
            "SELECT chain, from_address, to_address, value "
            "FROM chain_events ORDER BY block_timestamp DESC LIMIT 5"
        ),
    }


@router.post("/rag/confidence")
async def rag_confidence_score(request: Request, data: dict[str, Any]) -> dict[str, Any]:
    try:
        from rugbrain.confidence_scorer import score_confidence as sc

        sources = data.get("results", [])
        return sc(
            sources,
            query_similarity=sources[0].get("score", 0.0) if sources else 0.0,
        )
    except ImportError:
        from rugbrain.compat import score_confidence  # external dependency

        return score_confidence(
            data.get("results", []),
            query=data.get("query", ""),
            entity_matches=data.get("entity_matches", []),
        )


@router.post("/rag/chunk-solidity")
async def rag_chunk_solidity(request: Request, data: dict[str, Any]) -> dict[str, Any]:
    source = data.get("source", "")
    if not source:
        return {"error": "source is required"}
    from rugbrain.solidity_chunker import chunk_solidity_ast

    chunks = chunk_solidity_ast(source)
    return {"chunks": chunks, "count": len(chunks)}


@router.post("/scanner/deployer-risk")
async def scanner_deployer_risk(request: Request, data: dict[str, Any]) -> dict[str, Any]:
    """Deployer risk — 503 until a real scorer is wired.

    The compat stub returned a hardcoded risk_score of 0.5 with zero rugs and
    zero deploy age. Over HTTP that reads as "this deployer is medium risk",
    which is a fabricated accusation. Refuse instead of guessing.
    """
    from rugbrain.compat import DeployerScorerUnavailable, score_deployer_risk

    try:
        return score_deployer_risk(
            address=data.get("address", ""),
            deployment_count=data.get("deployment_count", 0),
            mixer_funded=data.get("mixer_funded", False),
            source_verified=data.get("source_verified", False),
            code_similarity=data.get("code_similarity", 0.0),
            rapid_deploy=data.get("rapid_deploy", False),
            funded_by_scammer=data.get("funded_by_scammer", False),
        )
    except DeployerScorerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/rag/cross-chain")
async def rag_cross_chain(request: Request, data: dict[str, Any]) -> list[dict[str, Any]]:
    from rugbrain.compat import resolve_cross_chain_identity  # external dependency

    return resolve_cross_chain_identity(
        address=data.get("address", ""),
        chain=data.get("chain", "ethereum"),
        funding_sources=data.get("funding_sources", []),
        label_hints=data.get("label_hints", []),
    )


@router.post("/rag/image-analysis")
async def rag_image_analysis(request: Request, data: dict[str, Any]) -> dict[str, Any]:
    from rugbrain.multimodal import describe_image

    return {
        "task": data.get("task", "describe"),
        "description": await describe_image(
            image_url=data.get("image_url", ""), task=data.get("task", "describe")
        ),
    }


@router.post("/rag/logo-check")
async def rag_logo_check(request: Request, data: dict[str, Any]) -> dict[str, Any]:
    url = data.get("image_url", "")
    if not url:
        return {"error": "image_url is required"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url)
    from rugbrain.multimodal import compute_image_hash, get_logo_db

    h = compute_image_hash(r.content)
    return {**get_logo_db().check_theft(h), "computed_hash": h}


@router.post("/rag/investigate")
async def rag_investigate_endpoint(request: Request, data: dict[str, Any]) -> dict[str, Any]:
    # Accept either ``query`` (natural language) or ``address`` (token/wallet).
    q = data.get("query", "") or data.get("address", "")
    if not q:
        return {"error": "query is required"}
    from rugbrain.compat import investigate  # external dependency

    return await investigate(
        query=q,
        investigation_type=data.get("type", "auto"),
        max_hops=data.get("max_hops", 5),
    )


@router.post("/rag/watch")
async def rag_create_watch(request: Request, data: dict[str, Any]) -> dict[str, Any]:
    from rugbrain.streaming import create_watch

    return await create_watch(
        token_address=data.get("token_address", ""), chain=data.get("chain", "ethereum")
    )


@router.get("/rag/watches")
async def rag_list_watches(request: Request) -> list[dict[str, Any]]:
    from rugbrain.streaming import list_active_watches

    return await list_active_watches()
