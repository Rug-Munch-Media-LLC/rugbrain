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
            ok = await store.insert(
                doc_id="doc_x", collection="rmi_documents", embedding=[0.1] * 2560
            )
        assert ok is False
        assert not put_route.called  # blocked before any HTTP write
        assert any(
            "qdrant_write_gate_blocked" in r.message and "rmi_documents" in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_owned_collection_passes_gate(self) -> None:
        """privacy_protocol is rugbrain-owned AND already canonical at 4096.

        It has no 2560 base to retire, so the lane resolver leaves the name
        untouched — which keeps this test focused on the gate's admit path
        rather than on routing. (``news_v2`` used to be used here, but it is
        migrated: it now resolves to ``news_v2_8b``. See the twin test below.)
        """
        store = QdrantVectorStore(url=URL)
        with respx.mock(base_url=URL, assert_all_called=False) as respx_mock:
            _mock_plain_collection(respx_mock, "privacy_protocol")
            put_route = respx_mock.put("/collections/privacy_protocol/points").respond(
                200, json={"result": {"status": "ok"}}
            )
            ok = await store.insert(
                doc_id="doc_owned", collection="privacy_protocol", embedding=[0.1] * 4096
            )
        assert ok is True
        assert put_route.called

    @pytest.mark.asyncio
    async def test_migrated_owned_collection_passes_gate_via_twin(self) -> None:
        """news_v2 is rugbrain-owned AND migrated: readonly must not block it.

        The resolver routes the base to its populated 4096 twin, and the gate
        admits the twin because ownership is a property of the corpus, not of
        the lane. Writing the 2560 base would be the defect this prevents.
        """
        store = QdrantVectorStore(url=URL)
        with respx.mock(base_url=URL, assert_all_called=False) as respx_mock:
            _mock_plain_collection(respx_mock, "news_v2_8b")
            put_route = respx_mock.put("/collections/news_v2_8b/points").respond(
                200, json={"result": {"status": "ok"}}
            )
            ok = await store.insert(
                doc_id="doc_owned", collection="news_v2", embedding=[0.1] * 4096
            )
        assert ok is True
        assert put_route.called

    @pytest.mark.asyncio
    async def test_gate_evaluates_canonical_name_with_interim_suffix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ownership is checked on the name WITHOUT the interim suffix:
        non-owned + suffix → still blocked; owned + suffix → passes.

        ``privacy_protocol`` is used for the admit case because it is owned and
        already canonical (no lane rewrite), keeping this test about the interim
        suffix. An owned MIGRATED name arrives lane-resolved, so the expected
        interim target is ``<name>_8b__interim`` — see the sibling test.
        """
        import rugbrain.qdrant_store as qdrant_store

        monkeypatch.setattr(qdrant_store, "_INTERIM_SUFFIX", "__interim")
        store = QdrantVectorStore(url=URL)
        with respx.mock(base_url=URL, assert_all_called=False) as respx_mock:
            blocked = await store.insert(
                doc_id="d1", collection="rmi_documents__interim", embedding=[0.1] * 2560
            )
            _mock_plain_collection(respx_mock, "privacy_protocol__interim")
            respx_mock.put("/collections/privacy_protocol__interim/points").respond(
                200, json={"result": {"status": "ok"}}
            )
            allowed = await store.insert(
                doc_id="d2", collection="privacy_protocol__interim", embedding=[0.1] * 4096
            )
        assert blocked is False
        assert allowed is True

    @pytest.mark.asyncio
    async def test_gate_evaluates_canonical_name_with_both_suffixes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lane suffix inside, interim outside — both must strip to the owner.

        news_v2 resolves to news_v2_8b, then the interim window shadows THAT,
        giving news_v2_8b__interim. The gate must still recognise the corpus as
        news_v2-owned. Interim is applied outermost precisely so the shadow is
        of the live lane, not of a retired one.
        """
        import rugbrain.qdrant_store as qdrant_store

        monkeypatch.setattr(qdrant_store, "_INTERIM_SUFFIX", "__interim")
        store = QdrantVectorStore(url=URL)
        with respx.mock(base_url=URL, assert_all_called=False) as respx_mock:
            _mock_plain_collection(respx_mock, "news_v2_8b__interim")
            respx_mock.put("/collections/news_v2_8b__interim/points").respond(
                200, json={"result": {"status": "ok"}}
            )
            allowed = await store.insert(doc_id="d3", collection="news_v2", embedding=[0.1] * 4096)
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
            ok = await store.insert(doc_id="d3", collection="scratch_coll", embedding=[0.1] * 2560)
        assert ok is True
        assert put_route.called
