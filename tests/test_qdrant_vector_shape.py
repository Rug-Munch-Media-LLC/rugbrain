"""The write path must send the vector shape the collection actually declares.

An UNNAMED collection (``params.vectors = {"size", "distance"}``) rejects a
named payload (``{"dense": [...]}``) with a 400, which is how 133/133 chunks
failed against ``research_v2_8b`` while a bare REST PUT succeeded.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rugbrain.qdrant_store import QdrantVectorStore

pytestmark = pytest.mark.unit

UNNAMED = {"size": 4, "distance": "Cosine"}
NAMED = {"dense": {"size": 4, "distance": "Cosine"}}

EMBEDDING = [0.1, 0.2, 0.3, 0.4]


class _Resp:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Answers the collection-config GET and captures the points PUT."""

    def __init__(self, vectors: dict[str, Any]) -> None:
        self.vectors = vectors
        self.sent: list[dict[str, Any]] = []

    async def get(self, path: str, **_: Any) -> _Resp:
        return _Resp({"result": {"config": {"params": {"vectors": self.vectors}}}})

    async def put(self, path: str, json: dict[str, Any] | None = None, **_: Any) -> _Resp:
        self.sent.append(json or {})
        return _Resp({"result": {"status": "completed"}})

    async def aclose(self) -> None:
        return None


def _store(monkeypatch: pytest.MonkeyPatch, vectors: dict[str, Any]) -> tuple[Any, _FakeClient]:
    import rugbrain.qdrant_store as pkg
    import rugbrain.qdrant_store.client_contract as contract
    import rugbrain.qdrant_store.write_guard as guard

    client = _FakeClient(vectors)
    store = QdrantVectorStore(url="http://127.0.0.1:6334")

    async def _get_client() -> _FakeClient:
        return client

    async def _true(*_a: Any, **_k: Any) -> bool:
        return True

    monkeypatch.setattr(store, "_get_client", _get_client, raising=False)
    monkeypatch.setattr(pkg, "_ensure_collection", _true, raising=False)
    monkeypatch.setattr(pkg, "_RMI_RAG_QDRANT_WRITE_MODE", "readwrite", raising=False)
    monkeypatch.setattr(guard, "slot_accepts", _true, raising=False)
    monkeypatch.setattr(
        contract, "require_qdrant_http_client", lambda c, where="": c, raising=False
    )
    return store, client


def test_unnamed_collection_sends_bare_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    store, client = _store(monkeypatch, UNNAMED)
    ok = asyncio.run(store.insert("vshape-unnamed", "vshape_probe", EMBEDDING, content="x"))
    assert ok is True, "insert must succeed against an unnamed collection"
    vector = client.sent[0]["points"][0]["vector"]
    assert vector == EMBEDDING
    assert isinstance(vector, list), f"unnamed collection needs a bare list, got {type(vector)}"


def test_named_collection_sends_named_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    store, client = _store(monkeypatch, NAMED)
    ok = asyncio.run(store.insert("vshape-named", "vshape_probe", EMBEDDING, content="x"))
    assert ok is True, "insert must succeed against a named-vector collection"
    vector = client.sent[0]["points"][0]["vector"]
    assert vector == {"dense": EMBEDDING}, (
        f"named collection needs {{'dense': [...]}}, got {vector}"
    )
