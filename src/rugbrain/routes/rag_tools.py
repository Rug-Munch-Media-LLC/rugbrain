"""RAG tool routes: parquet SQL, confidence, chunking, identity, multimodal,
investigation, watches.

Split out of ``rugbrain/endpoints.py`` (degodding phase 3, 2026-08-21).
No URL prefix here — the parent router in ``endpoints.py`` supplies
``/api/v1``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rag-optimization"])


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
    from rugbrain.chain_parquet import (
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
    from rugbrain.compat import score_deployer_risk  # external dependency

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
