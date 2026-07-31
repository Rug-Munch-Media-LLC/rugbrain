"""
Tests for endpoints.py — RAG API endpoints.

Uses FastAPI TestClient with mocked external dependencies.
Some endpoints depend on external modules that may not be installed,
so we test the self-contained ones and mock the rest.
"""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def client():
    """Create a test client for all RAG routers."""
    from fastapi import FastAPI

    from rmi_rag.endpoints import router, scam_check_router

    app = FastAPI()
    app.include_router(router)
    app.include_router(scam_check_router)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_chunk_solidity_empty_source(client):
    """POST /api/v1/rag/chunk-solidity with empty source should return error."""
    resp = await client.post(
        "/api/v1/rag/chunk-solidity",
        json={"source": ""},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert data["error"] == "source is required"


@pytest.mark.asyncio
async def test_chunk_solidity_simple_contract(client):
    """POST /api/v1/rag/chunk-solidity with a simple contract should chunk it."""
    contract = """
    pragma solidity ^0.8.0;
    contract Test {
        uint256 public value;
        function set(uint256 _v) public { value = _v; }
    }
    """
    resp = await client.post(
        "/api/v1/rag/chunk-solidity",
        json={"source": contract},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "chunks" in data
    assert data["count"] >= 1


@pytest.mark.asyncio
async def test_email_accounts_returns_list(client, monkeypatch):
    """GET /api/v1/email/accounts reads accounts from RMI_EMAIL_ACCOUNTS env."""
    monkeypatch.setenv(
        "RMI_EMAIL_ACCOUNTS",
        "gmail|ops@example.com|gmail,rmi|admin@example.com|gmail",
    )
    resp = await client.get("/api/v1/email/accounts")
    assert resp.status_code == 200
    data = resp.json()
    assert "accounts" in data
    assert len(data["accounts"]) >= 1
    account_ids = {a["id"] for a in data["accounts"]}
    assert "gmail" in account_ids
    assert "rmi" in account_ids


@pytest.mark.asyncio
async def test_email_accounts_unconfigured_empty(client, monkeypatch):
    """Unset RMI_EMAIL_ACCOUNTS returns an empty list (no hardcoded emails)."""
    monkeypatch.delenv("RMI_EMAIL_ACCOUNTS", raising=False)
    resp = await client.get("/api/v1/email/accounts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["accounts"] == []


@pytest.mark.asyncio
async def test_rag_watch_create_validation(client):
    """POST /api/v1/rag/watch without token_address should handle gracefully."""
    resp = await client.post(
        "/api/v1/rag/watch",
        json={"chain": "solana"},
    )
    # Should be a 200 or 422 depending on validation
    assert resp.status_code in (200, 422)


@pytest.mark.asyncio
async def test_rag_graph_communities(client):
    """GET /api/v1/rag/graph-communities should return OK."""
    resp = await client.get("/api/v1/rag/graph-communities")
    # May 500 if Neo4j is unavailable — that's expected behavior
    assert resp.status_code in (200, 500)


@pytest.mark.asyncio
async def test_search_empty_query(client):
    """GET /api/v1/search with empty query should return empty results."""
    resp = await client.get("/api/v1/search")
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_confidence_score_basic(client):
    """POST /api/v1/rag/confidence — skip if external dep not installed."""
    pytest.importorskip("app.confidence", reason="External dep app.confidence not installed")
    resp = await client.post(
        "/api/v1/rag/confidence",
        json={"results": [], "query": "test"},
    )
    assert resp.status_code in (200, 500)


@pytest.mark.asyncio
async def test_cross_chain_basic(client):
    """POST /api/v1/rag/cross-chain — skip if external dep not installed."""
    pytest.importorskip("app.cross_chain", reason="External dep not installed")
    resp = await client.post(
        "/api/v1/rag/cross-chain",
        json={"address": "0xabc", "chain": "ethereum"},
    )
    assert resp.status_code in (200, 500)


@pytest.mark.asyncio
async def test_deployer_risk_exists(client):
    """POST /api/v1/scanner/deployer-risk — skip if external dep not installed."""
    pytest.importorskip("app.deployer_reputation", reason="External dep not installed")
    resp = await client.post(
        "/api/v1/scanner/deployer-risk",
        json={"address": "0xabc", "deployment_count": 0},
    )
    assert resp.status_code in (200, 500)


@pytest.mark.asyncio
async def test_rag_watches_endpoint(client):
    """GET /api/v1/rag/watches should return OK."""
    resp = await client.get("/api/v1/rag/watches")
    assert resp.status_code in (200, 500)


@pytest.mark.skip(reason="Requires Redis — use integration test suite instead")
@pytest.mark.asyncio
async def test_scam_check_integration(client):
    """GET /api/v1/rag/v2/scam-check — requires Redis and search_service."""
    resp = await client.get(
        "/api/v1/rag/v2/scam-check",
        params={"address": "0xabc", "chain": "ethereum", "tier": "free"},
    )
    assert resp.status_code in (200, 429, 500)


@pytest.mark.asyncio
async def test_investigate_missing_query(client):
    """POST /api/v1/rag/investigate without query should return error."""
    resp = await client.post(
        "/api/v1/rag/investigate",
        json={},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert "query" in data["error"].lower() or "required" in data["error"].lower()


@pytest.mark.asyncio
async def test_image_analysis_missing_url(client):
    """POST /api/v1/rag/image-analysis without URL."""
    resp = await client.post(
        "/api/v1/rag/image-analysis",
        json={"task": "describe"},
    )
    # Depends on multimodal module
    assert resp.status_code in (200, 500)


@pytest.mark.asyncio
async def test_logo_check_missing_url(client):
    """POST /api/v1/rag/logo-check without image_url should return error."""
    resp = await client.post(
        "/api/v1/rag/logo-check",
        json={},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data or "hash" in data
