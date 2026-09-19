"""Write-gate tests: non-owned collections blocked in readonly mode.

The gate lives in ``store_write.WriteMixin.insert`` and evaluates ownership on
the CANONICAL collection name (interim suffix stripped) against
``_RMI_RAG_OWNED_COLLECTIONS``. Qdrant HTTP is fully mocked via respx — no
live network.
"""

from __future__ import annotations

from typing import Any

import pytest
import respx

from rugbrain.qdrant_store import QdrantVectorStore

URL = "http://rmi-qdrant:6333"


def _mock_plain_collection(respx_mock: Any, collection: str) -> None:
    """Collection exists with a bare (unnamed-vector) config."""
    respx_mock.get(f"/collections/{collection}").respond(
        200, json={"result": {"config": {"params": {}}}}
    )


class TestQdrantWriteGate:
    """readonly mode blocks non-owned collections; owned ones pass."""

    @pytest.mark.asyncio
    async def test_non_owned_collection_blocked(self, caplog: pytest.LogCaptureFixture) -> None:
        """rmi_documents is owned by rmi-backend — rugbrain must never write."""
        store = QdrantVectorStore(url=URL)
        with respx.mock(base_url=URL, assert_all_called=False) as respx_mock:
            put_route = respx_mock.put("/collections/rmi_documents/points")
            ok = await store.insert(doc_id="doc_x", collection="rmi_documents", embedding=[0.1] * 8)
        assert ok is False
        assert not put_route.called  # blocked before any HTTP write
        assert any(
            "qdrant_write_gate_blocked" in r.message and "rmi_documents" in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_owned_collection_passes_gate(self) -> None:
        """news_v2 is rugbrain-owned — readonly mode must NOT block it."""
        store = QdrantVectorStore(url=URL)
        with respx.mock(base_url=URL, assert_all_called=False) as respx_mock:
            _mock_plain_collection(respx_mock, "news_v2")
            put_route = respx_mock.put("/collections/news_v2/points").respond(
                200, json={"result": {"status": "ok"}}
            )
            ok = await store.insert(doc_id="doc_owned", collection="news_v2", embedding=[0.1] * 8)
        assert ok is True
        assert put_route.called

    @pytest.mark.asyncio
    async def test_gate_evaluates_canonical_name_with_interim_suffix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ownership is checked on the name WITHOUT the interim suffix:
        non-owned + suffix → still blocked; owned + suffix → passes."""
        import rugbrain.qdrant_store as qdrant_store

        monkeypatch.setattr(qdrant_store, "_INTERIM_SUFFIX", "__interim")
        store = QdrantVectorStore(url=URL)
        with respx.mock(base_url=URL, assert_all_called=False) as respx_mock:
            blocked = await store.insert(
                doc_id="d1", collection="rmi_documents__interim", embedding=[0.1] * 8
            )
            _mock_plain_collection(respx_mock, "news_v2__interim")
            respx_mock.put("/collections/news_v2__interim/points").respond(
                200, json={"result": {"status": "ok"}}
            )
            allowed = await store.insert(
                doc_id="d2", collection="news_v2__interim", embedding=[0.1] * 8
            )
        assert blocked is False
        assert allowed is True

    @pytest.mark.asyncio
    async def test_writable_mode_disables_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """mode != readonly → non-owned collections are writable."""
        import rugbrain.qdrant_store as qdrant_store

        monkeypatch.setattr(qdrant_store, "_RMI_RAG_QDRANT_WRITE_MODE", "writable")
        store = QdrantVectorStore(url=URL)
        with respx.mock(base_url=URL, assert_all_called=False) as respx_mock:
            _mock_plain_collection(respx_mock, "scratch_coll")
            put_route = respx_mock.put("/collections/scratch_coll/points").respond(
                200, json={"result": {"status": "ok"}}
            )
            ok = await store.insert(doc_id="d3", collection="scratch_coll", embedding=[0.1] * 8)
        assert ok is True
        assert put_route.called
