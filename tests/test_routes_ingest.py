"""Audience enforcement for the public ingestion route."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from rugbrain.routes import ingest as ingest_routes


@pytest.mark.asyncio
async def test_ingest_rejects_internal_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest_routes, "_check_rate_limit", _allow_rate_limit)

    async def fail_embedding(_text: str) -> list[float]:
        raise AssertionError("embedding must not run for a denied collection")

    import rugbrain.embeddings as embeddings

    monkeypatch.setattr(embeddings, "get_embedding", fail_embedding, raising=False)

    app = FastAPI()
    app.include_router(ingest_routes.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ingest",
            json={
                "documents": [
                    {
                        "content": "internal source",
                        "collection": "codebase_reference",
                    }
                ]
            },
        )

    assert response.status_code == 403
    assert response.json() == {"detail": {"error": "collection_not_available"}}


@pytest.mark.asyncio
async def test_ingest_sanitizes_public_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ingest_routes, "_check_rate_limit", _allow_rate_limit)
    inserted: dict[str, Any] = {}

    async def fake_embedding(_text: str) -> list[float]:
        return [0.1] * 8

    class FakeStore:
        async def insert(self, **kwargs: Any) -> bool:
            inserted.update(kwargs)
            return True

    import rugbrain.embeddings as embeddings
    import rugbrain.qdrant_store as qd_mod

    monkeypatch.setattr(embeddings, "get_embedding", fake_embedding, raising=False)
    monkeypatch.setattr(qd_mod, "QdrantVectorStore", lambda: FakeStore(), raising=False)

    app = FastAPI()
    app.include_router(ingest_routes.router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ingest",
            json={
                "documents": [
                    {
                        "doc_id": "public-1",
                        "content": "A public incident writeup",
                        "collection": "defi_exploits",
                        "source": "https://example.com/report",
                        "metadata": {
                            "file_path": "src/internal/service.py",
                            "model": "private-model",
                            "source": "https://example.com/report",
                        },
                    }
                ]
            },
        )

    assert response.status_code == 200
    assert response.json()["ingested"] == 1
    assert inserted["collection"] == "defi_exploits"
    assert inserted["metadata"] == {"source": "https://example.com/report"}


async def _allow_rate_limit(*_args: Any, **_kwargs: Any) -> tuple[bool, int]:
    return True, 0
