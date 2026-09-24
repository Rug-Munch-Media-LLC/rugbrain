"""Tests for Crypto Code Mode (ContextCrypto collections retrieval).

All external services (Qdrant, embedder) are mocked — no network.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from rugbrain import crypto_code_retrieval as ccr
from rugbrain.crypto_code_retrieval import (
    CryptoCodeRetriever,
    classify_crypto_code_query,
    search_crypto_code,
)

# ── 1. classification ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "how do I write a reentrancy guard in Solidity",
        "show me an exploit PoC for the ERC-20 approval bug",
        "foundry test for a flash loan attack",
        "anchor PDA security best practices",
        "uniswap v3 swap callback code",
        "cargo test the CPI error handling in rust",
    ],
)
def test_classify_positive(query: str) -> None:
    assert classify_crypto_code_query(query)


@pytest.mark.parametrize(
    "query",
    [
        "bitcoin price today",
        "is 0xabc a scam token",
        "ethereum whale activity this week",
        "",
        "what is the fear and greed index",
    ],
)
def test_classify_negative(query: str) -> None:
    assert not classify_crypto_code_query(query)


# ── 2. retrieval with mocked Qdrant + embedder ────────────────────────


def _point(pid: str, score: float, payload: dict[str, Any]) -> dict[str, Any]:
    return {"id": pid, "score": score, "payload": payload}


class FakeEmbedder:
    async def embed_one(self, text: str) -> list[float]:
        return [0.1] * 2560


class _Resp:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class FakeClient:
    """Async context-manager httpx stand-in."""

    def __init__(self, points_by_collection: dict[str, list[dict[str, Any]]]) -> None:
        self.points = points_by_collection
        self.filters_seen: list[dict[str, Any] | None] = []

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, path: str, json: dict[str, Any]) -> _Resp:
        for coll in ccr.CONTEXTCRYPTO_COLLECTIONS:
            if path == f"/collections/{coll}/points/search":
                self.filters_seen.append(json.get("filter"))
                return _Resp(200, {"result": self.points.get(coll, [])})
        return _Resp(404, {"detail": "not found"})


REF_PAYLOAD = {
    "title": "Reentrancy guard",
    "description": "nonReentrant modifier from openzeppelin",
    "code_block": "modifier nonReentrant() { ... }",
    "library_id": "openzeppelin/contracts",
    "library_name": "OpenZeppelin Contracts",
    "file_path": "utils/ReentrancyGuard.sol",
    "snippet_type": "function",
    "language": "solidity",
    "verdict": "clean",
    "category": "smart-contracts",
    "ecosystem": "evm",
    "source": "https://github.com/openzeppelin/contracts",
    "content": "modifier nonReentrant",
}
SEC_PAYLOAD = {
    "title": "Reentrancy exploit PoC",
    "description": "drains the vault via fallback receive",
    "code_block": "contract Drain { ... }",
    "verdict": "malicious",
    "category": "Reentrancy",
    "severity": "critical",
    "ecosystem": "EVM",
    "tags": "reentrancy,flash-loan",
    "content": "reentrancy drain vault",
    "reason_why": "external call before state update",
}


@pytest.fixture
def retriever() -> CryptoCodeRetriever:
    return CryptoCodeRetriever(embedder=FakeEmbedder(), base_url="http://qdrant-test:6333")


@pytest.mark.asyncio
async def test_retrieve_fuses_and_attributes(retriever: CryptoCodeRetriever) -> None:
    points = {
        "codebase_reference": [_point("ref-1", 0.92, REF_PAYLOAD)],
        "security_patterns": [_point("sec-1", 0.88, SEC_PAYLOAD)],
    }
    retriever._client_factory = None  # not used; monkeypatch httpx below

    import httpx

    original = httpx.AsyncClient
    httpx.AsyncClient = lambda **kwargs: FakeClient(points)  # type: ignore[misc]
    try:
        out = await retriever.retrieve("reentrancy guard solidity", top_k=5)
    finally:
        httpx.AsyncClient = original  # type: ignore[assignment]

    assert out["total"] == 2
    ids = {r["id"] for r in out["results"]}
    assert ids == {"ref-1", "sec-1"}
    by_id = {r["id"]: r for r in out["results"]}
    ref = by_id["ref-1"]
    assert ref["library"] == "openzeppelin/contracts"
    assert ref["file_path"] == "utils/ReentrancyGuard.sol"
    assert ref["verdict"] == "clean"
    assert ref["verdict_label"] == "reference implementation (clean)"
    assert ref["source_url"].startswith("https://github.com/")
    sec = by_id["sec-1"]
    assert sec["verdict_label"] == "exploit / malicious pattern"
    assert sec["reason_why"] == "external call before state update"
    assert sec["severity"] == "critical"
    # rrf fusion populated
    assert all(r["rrf_score"] > 0 for r in out["results"])


@pytest.mark.asyncio
async def test_retrieve_applies_filters(retriever: CryptoCodeRetriever) -> None:
    points: dict[str, list[dict[str, Any]]] = {
        "codebase_reference": [_point("ref-1", 0.9, REF_PAYLOAD)],
        "security_patterns": [],
    }
    import httpx

    client = FakeClient(points)
    original = httpx.AsyncClient
    httpx.AsyncClient = lambda **kwargs: client  # type: ignore[misc,assignment]
    try:
        await retriever.retrieve(
            "reentrancy", top_k=5, verdict="clean", library_id="openzeppelin/contracts"
        )
    finally:
        httpx.AsyncClient = original  # type: ignore[assignment]

    assert client.filters_seen
    for f in client.filters_seen:
        assert f["match"]["verdict"] == "clean"
        assert f["match"]["library_id"] == "openzeppelin/contracts"


@pytest.mark.asyncio
async def test_retrieve_degrades_on_qdrant_error() -> None:
    class DeadClient(FakeClient):
        async def post(self, path: str, json: dict[str, Any]) -> Any:
            raise RuntimeError("qdrant down")

    import httpx

    r = CryptoCodeRetriever(embedder=FakeEmbedder(), base_url="http://qdrant-test:6333")
    original = httpx.AsyncClient
    httpx.AsyncClient = lambda **kwargs: DeadClient({})  # type: ignore[misc,assignment]
    try:
        out = await r.retrieve("solidity reentrancy", top_k=5)
    finally:
        httpx.AsyncClient = original  # type: ignore[assignment]
    assert out["total"] == 0
    assert out["results"] == []


# ── 3. route logic ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_crypto_code_not_routed(monkeypatch: pytest.MonkeyPatch) -> None:
    out = await search_crypto_code("bitcoin price today")
    assert out["routed"] is False
    assert out["total"] == 0


@pytest.mark.asyncio
async def test_search_crypto_code_routed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_retrieve(self: Any, query: str, **kw: Any) -> dict[str, Any]:
        return {
            "query": query,
            "collections": list(ccr.CONTEXTCRYPTO_COLLECTIONS),
            "results": [{"id": "ref-1", "rrf_score": 0.03}],
            "total": 1,
        }

    monkeypatch.setattr(CryptoCodeRetriever, "retrieve", fake_retrieve)
    out = await search_crypto_code("solidity reentrancy guard example")
    assert out["routed"] is True
    assert out["total"] == 1


@pytest.mark.asyncio
async def test_augment_coding_results_merges(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(query: str, **kw: Any) -> dict[str, Any]:
        return {
            "results": [
                {
                    "id": "sec-1",
                    "collection": "security_patterns",
                    "code_block": "contract Drain {}",
                    "description": "poc",
                    "source_url": "https://x",
                    "library": "",
                    "verdict": "malicious",
                    "verdict_label": "exploit / malicious pattern",
                    "file_path": "",
                    "reason_why": "external call first",
                    "rrf_score": 0.05,
                    "dense_score": 0.9,
                }
            ]
        }

    monkeypatch.setattr(ccr, "search_crypto_code", fake_search)
    merged = await ccr.augment_coding_results(
        "reentrancy exploit code",
        [{"content": "prose", "similarity": 0.5, "rrf_score": 0.01}],
        audience="internal",
    )
    assert any(r.get("contextcrypto") for r in merged)
    assert merged[0]["rrf_score"] >= merged[-1]["rrf_score"]


@pytest.mark.asyncio
async def test_augment_passthrough_non_code_query() -> None:
    base = [{"content": "prose", "similarity": 0.5}]
    merged = await ccr.augment_coding_results("bitcoin price", base)
    assert merged == base


# ── 4. endpoint ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_endpoint_routed_to_public_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    from rugbrain import public_crypto_code as public_code
    from rugbrain.routes import search as search_routes

    async def fake_public_search(query: str, **kw: Any) -> dict[str, Any]:
        return {"query": query, "routed": True, "results": [], "total": 0}

    monkeypatch.setattr(public_code, "search_public_crypto_code", fake_public_search)

    app = FastAPI()
    app.include_router(search_routes.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/search/crypto-code", params={"q": "solidity reentrancy"})
    assert resp.status_code == 200
    assert resp.json()["routed"] is True


@pytest.mark.asyncio
async def test_endpoint_refuses_operational_exploit_request() -> None:
    from rugbrain.routes import search as search_routes

    app = FastAPI()
    app.include_router(search_routes.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/search/crypto-code",
            params={"q": "deploy an exploit against a live mainnet wallet"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] is True
    assert body["results"] == []
