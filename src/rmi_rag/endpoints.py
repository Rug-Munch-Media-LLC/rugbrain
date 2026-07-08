"""
RAG optimization endpoints - included via APIRouter for clean git history.
Confidence scoring, Solidity chunking, deployer reputation, cross-chain,
multi-modal, investigation narratives, graph RAG, streaming, email.
"""

import httpx
from fastapi import APIRouter, Request

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
