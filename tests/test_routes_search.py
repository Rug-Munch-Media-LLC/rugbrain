"""Route-level contracts for /api/v1/search + /search/multi (routes/search.py).

FastAPI ASGI tests with the embedder + Qdrant store mocked at their
source modules (the routes import them lazily inside the handler).
No live services.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def client() -> AsyncClient:
    from fastapi import FastAPI

    from rugbrain.routes.search import router

    app = FastAPI()
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _hit(id_: str, score: float, title: str) -> dict[str, Any]:
    meta = {"title": title, "severity": "high", "chain": "ethereum"}
    return {
        "id": id_,
        "score": score,
        "payload": dict(meta),
        "metadata": dict(meta),
        "content": f"report about {title}",
    }


@pytest.fixture
def mock_rag(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    """Patch embeddings + store; record (collection, limit) calls."""
    calls: list[tuple[str, int]] = []

    async def fake_emb(_q: str) -> list[float]:
        return [0.1] * 8

    class FakeStore:
        async def hybrid_search(
            self, *, collection: str, limit: int, **_: Any
        ) -> list[dict[str, Any]]:
            calls.append((collection, limit))
            if collection == "threat_intel_v2":
                return [_hit("t1", 0.9, "Flash loan attack"), _hit("t2", 0.7, "Reentrancy")]
            return [_hit("s1", 0.8, "Phishing kit")]

    import rugbrain.embeddings as emb_mod
    import rugbrain.qdrant_store as qd_mod

    monkeypatch.setattr(emb_mod, "get_query_embedding", fake_emb, raising=False)
    monkeypatch.setattr(qd_mod, "QdrantVectorStore", lambda: FakeStore(), raising=False)
    return calls


# ── GET /search ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_returns_hits_with_filters_passthrough(client: AsyncClient, mock_rag) -> None:
    resp = await client.get(
        "/search",
        params={"q": "flash loan", "filter_severity": "high", "limit": 5},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == len(data["results"]) == 2
    assert data["collection"] == "threat_intel_v2"
    assert data["query"] == "flash loan"
    assert mock_rag[0][0] == "threat_intel_v2"


@pytest.mark.asyncio
async def test_search_empty_query_short_circuits(client: AsyncClient, mock_rag) -> None:
    resp = await client.get("/search", params={"q": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert data["results"] == [] and data["total"] == 0
    assert mock_rag == []  # no store call


@pytest.mark.asyncio
async def test_search_rejects_internal_collection(client: AsyncClient) -> None:
    resp = await client.get(
        "/search",
        params={"q": "reentrancy", "collection": "codebase_reference"},
    )
    assert resp.status_code == 403
    assert resp.json() == {"detail": {"error": "collection_not_available"}}


@pytest.mark.asyncio
async def test_search_sanitizes_internal_fields(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_emb(_q: str) -> list[float]:
        return [0.1] * 8

    class FakeStore:
        async def hybrid_search(self, **_: Any) -> list[dict[str, Any]]:
            return [
                {
                    "id": "public-1",
                    "content": "Qwen model at /srv/work/repos/internal returned a report",
                    "metadata": {
                        "file_path": "src/internal/service.py",
                        "model": "private-model",
                        "source": "https://example.com/audit",
                    },
                }
            ]

    import rugbrain.embeddings as emb_mod
    import rugbrain.qdrant_store as qd_mod

    monkeypatch.setattr(emb_mod, "get_query_embedding", fake_emb, raising=False)
    monkeypatch.setattr(qd_mod, "QdrantVectorStore", lambda: FakeStore(), raising=False)

    resp = await client.get("/search", params={"q": "reentrancy"})
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert "metadata" not in result or "file_path" not in result["metadata"]
    assert "model" not in result.get("metadata", {})
    assert "/srv/" not in result["content"]


@pytest.mark.asyncio
async def test_search_fails_loud_503_when_embedder_down(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Search must FAIL LOUD (503), not serve an empty page that reads as
    "no threats found" — a silent empty result set is dangerous for a
    security product."""
    import rugbrain.embeddings as emb_mod

    async def boom(_q: str) -> list[float]:
        raise RuntimeError("embedder down")

    monkeypatch.setattr(emb_mod, "get_query_embedding", boom, raising=False)
    resp = await client.get("/search", params={"q": "anything"})
    assert resp.status_code == 503


# ── POST /search/multi ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multi_search_fuses_across_collections(client: AsyncClient, mock_rag) -> None:
    resp = await client.post(
        "/search/multi",
        json={
            "query": "attack",
            "collections": ["threat_intel_v2", "scam_patterns"],
            "limit": 10,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert {c for c, _ in mock_rag} == {"threat_intel_v2", "scam_patterns"}
    assert data["total"] >= 2  # fused from both collections


@pytest.mark.asyncio
async def test_multi_search_empty_query_no_calls(client: AsyncClient, mock_rag) -> None:
    resp = await client.post("/search/multi", json={"query": ""})
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert mock_rag == []
