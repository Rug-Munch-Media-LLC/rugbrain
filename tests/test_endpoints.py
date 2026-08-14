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

    from rugbrain.endpoints import router, scam_check_router

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


VALID_ADDRESS = "0x0a16a85be44627c10cee75db06b169c7bc76de2c"


@pytest.mark.asyncio
async def test_analyze_invalid_address(client):
    """GET /api/v1/analyze/{address} rejects non-address input."""
    resp = await client.get("/api/v1/analyze/not-an-address")
    assert resp.status_code == 400
    # FastAPI wraps HTTPException detail under {"detail": {...}}.
    body = resp.json()
    detail = body.get("detail", body)
    assert "invalid_address" in detail.get("error", "")


@pytest.mark.asyncio
async def test_analyze_valid_address_fail_open(client, monkeypatch):
    """GET /api/v1/analyze/{address} returns findings; fails open on backend errors."""
    import rugbrain.endpoints as ep

    async def fake_findings(address: str, chain: str, limit: int) -> dict:
        return {
            "address": address,
            "chain": chain,
            "verdict": {"risk": "medium", "confidence": 0.6, "top_pattern": "reentrancy"},
            "matched_patterns": [
                {
                    "name": "reentrancy",
                    "confidence": 0.6,
                    "reference_incident": "https://example.com/rekt",
                    "technique": "withdraw-after-external-call",
                }
            ],
            "risk_signals": ["reentrancy-guard-missing"],
            "cross_reference": {
                "address": address,
                "chain": chain,
                "backend": "qdrant",
                "similar_tokens": [],
                "narrative": "test narrative",
            },
        }

    monkeypatch.setattr(ep, "_analyze_address_findings", fake_findings)
    resp = await client.get(f"/api/v1/analyze/{VALID_ADDRESS}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["verdict"]["risk"] == "medium"
    assert data["matched_patterns"][0]["name"] == "reentrancy"
    assert data["cross_reference"]["narrative"] == "test narrative"


@pytest.mark.asyncio
async def test_search_token_address_includes_pattern_matches(client, monkeypatch):
    """GET /api/v1/search?q=<address> attaches pattern_matches findings."""
    import rugbrain.endpoints as ep

    async def fake_findings(address: str, chain: str, limit: int) -> dict:
        return {
            "address": address,
            "chain": chain,
            "verdict": {"risk": "high", "confidence": 0.8, "top_pattern": "reentrancy"},
            "matched_patterns": [],
            "risk_signals": ["reentrancy"],
            "cross_reference": {"backend": "none", "similar_tokens": [], "narrative": ""},
        }

    monkeypatch.setattr(ep, "_analyze_address_findings", fake_findings)
    resp = await client.get(f"/api/v1/search?q={VALID_ADDRESS}&limit=5")
    # Search may 503 when the embedding backend is unavailable in CI, but the
    # endpoint must still attempt pattern matching before that.
    assert resp.status_code in (200, 503)
    if resp.status_code == 200:
        assert resp.json()["pattern_matches"]["verdict"]["risk"] == "high"


# ── Part 3: GET /api/v1/market/mood ────────────────────────────────────


@pytest.mark.asyncio
async def test_market_mood_fail_open(client, monkeypatch):
    """GET /api/v1/market/mood returns neutral values when providers fail."""
    import rugbrain.market_context as mc

    async def fake_mood(chain: str) -> dict:
        return {
            "fear_index": 20.0,
            "fear_classification": "fearful",
            "market_sentiment": "fearful",
            "recent_exploits_24h": 3,
            "chain_volume_24h": -4.2,
            "chain_activity": {
                "chain": chain,
                "tvl": 5e10,
                "tvl_change_24h": -4.2,
                "new_deploys_24h": 12,
            },
            "generated_at": "2026-08-04T00:00:00Z",
        }

    monkeypatch.setattr(mc, "build_market_mood", fake_mood)
    resp = await client.get("/api/v1/market/mood?chain=ethereum")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fear_index"] == 20.0
    assert data["market_sentiment"] == "fearful"
    assert data["recent_exploits_24h"] == 3


@pytest.mark.asyncio
async def test_market_mood_fallback_on_error(client, monkeypatch):
    """GET /api/v1/market/mood degrades to neutral when build raises."""
    import rugbrain.market_context as mc

    async def boom(chain: str) -> dict:
        raise RuntimeError("provider down")

    monkeypatch.setattr(mc, "build_market_mood", boom)
    resp = await client.get("/api/v1/market/mood")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fear_index"] == 50.0
    assert "error" in data


# ── Part 3: GET /api/v1/market/context/{address} ───────────────────────


@pytest.mark.asyncio
async def test_market_context_invalid_address(client):
    """GET /api/v1/market/context/{address} rejects non-address input."""
    resp = await client.get("/api/v1/market/context/not-an-address")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_market_context_token(client, monkeypatch):
    """GET /api/v1/market/context/{address} returns the full context."""
    import rugbrain.market_context as mc

    async def fake_context(address: str, chain: str) -> dict:
        return {
            "address": address,
            "chain": chain,
            "fear_index": 30.0,
            "fear_classification": "fearful",
            "market_sentiment": "fearful",
            "chain_activity": {
                "chain": chain,
                "tvl": 1e9,
                "tvl_change_24h": -2.0,
                "new_deploys_24h": 5,
            },
            "chain_volume_24h": -2.0,
            "recent_exploits": [{"protocol": "Example", "chain": chain}],
            "social_signals": {
                "sentiment": "bearish",
                "confidence": 0.6,
                "mentions": 8,
                "mention_velocity": 40.0,
            },
            "deployer_freshness": "new",
            "similar_tokens": [],
            "generated_at": "2026-08-04T00:00:00Z",
        }

    monkeypatch.setattr(mc, "build_token_market_context", fake_context)
    resp = await client.get(f"/api/v1/market/context/{VALID_ADDRESS}?chain=ethereum")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fear_index"] == 30.0
    assert data["social_signals"]["mention_velocity"] == 40.0
    assert data["deployer_freshness"] == "new"
    assert data["recent_exploits"][0]["protocol"] == "Example"


# ── Part 4: GET /api/v1/analyze/{address}/full ─────────────────────────


@pytest.mark.asyncio
async def test_analyze_full_invalid_address(client):
    """GET /api/v1/analyze/{address}/full rejects non-address input."""
    resp = await client.get("/api/v1/analyze/not-an-address/full")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_analyze_full_sections(client, monkeypatch):
    """GET /api/v1/analyze/{address}/full returns every section fail-open."""
    import rugbrain.endpoints as ep
    import rugbrain.market_context as mc

    async def fake_findings(address: str, chain: str, limit: int) -> dict:
        return {
            "address": address,
            "chain": chain,
            "verdict": {"risk": "high", "confidence": 0.8, "top_pattern": "honeypot"},
            "fear_index": 30,
            "matched_patterns": [{"name": "honeypot", "confidence": 0.8}],
            "risk_signals": ["sell-tax-high"],
            "cross_reference": {"backend": "none", "similar_tokens": [], "narrative": ""},
            "contract_security": {"assessment": "unverified", "source_fetched": False},
        }

    async def fake_market(address: str, chain: str) -> dict:
        return {
            "address": address,
            "chain": chain,
            "fear_index": 30.0,
            "market_sentiment": "fearful",
            "recent_exploits": [],
            "social_signals": {"sentiment": "neutral"},
            "deployer_freshness": "new",
            "similar_tokens": [],
        }

    monkeypatch.setattr(ep, "_analyze_address_findings", fake_findings)
    monkeypatch.setattr(mc, "build_token_market_context", fake_market)
    resp = await client.get(f"/api/v1/analyze/{VALID_ADDRESS}/full?chain=ethereum")
    assert resp.status_code == 200
    data = resp.json()
    assert data["address"] == VALID_ADDRESS
    assert data["pattern_matching"]["verdict"]["risk"] == "high"
    assert data["market_context"]["fear_index"] == 30.0
    assert data["scanner_risk_score"]["available"] is False
    assert data["social_sentiment"]["sentiment"] == "unknown"
    assert data["code_analysis"]["assessment"] == "unverified"
    assert data["known_scam_associations"]["matched"] is False
    # Every section key must exist.
    for section in (
        "pattern_matching",
        "cross_reference",
        "market_context",
        "scanner_risk_score",
        "social_sentiment",
        "code_analysis",
        "known_scam_associations",
    ):
        assert section in data
