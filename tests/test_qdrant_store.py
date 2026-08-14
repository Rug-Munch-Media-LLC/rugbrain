"""
Tests for qdrant_store.py — Qdrant vector store operations.

Uses respx to mock HTTP calls to the Qdrant REST API.
"""

import uuid

import pytest
import respx


def _mock_collection(respx_mock, collection="default", exists=True):
    """Mock collection existence check."""
    status = 200 if exists else 404
    respx_mock.get(f"/collections/{collection}").respond(
        status, json={"status": "ok" if exists else "not found"}
    )


def _mock_search(respx_mock, results: list[dict], collection="default"):
    """Mock search endpoint with given results."""
    respx_mock.post(f"/collections/{collection}/points/search").respond(
        200, json={"result": results}
    )


@pytest.mark.asyncio
async def test_search_returns_results():
    """search() should return formatted results from Qdrant."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock)
        _mock_search(
            respx_mock,
            [
                {
                    "id": "doc1",
                    "score": 0.85,
                    "payload": {
                        "content": "Test document",
                        "metadata": {"key": "val"},
                        "source": "test",
                        "severity": "high",
                    },
                }
            ],
        )
        results = await store.search(query_embedding=[0.1] * 768, limit=5, min_similarity=0.5)
    assert len(results) == 1
    assert results[0]["id"] == "doc1"
    assert results[0]["similarity"] == 0.85
    assert results[0]["content"] == "Test document"
    assert results[0]["metadata"]["key"] == "val"
    assert results[0]["source"] == "test"
    assert results[0]["severity"] == "high"


@pytest.mark.asyncio
async def test_search_empty_when_no_results():
    """search() should return empty list when no results found."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock)
        _mock_search(respx_mock, [])
        results = await store.search(query_embedding=[0.1] * 768, limit=5, min_similarity=0.5)
    assert results == []


@pytest.mark.asyncio
async def test_search_with_filters():
    """search() should pass filters to Qdrant."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock)
        route = respx_mock.post("/collections/default/points/search").respond(
            200, json={"result": []}
        )
        await store.search(
            query_embedding=[0.1] * 768,
            limit=5,
            filters={"chain": "solana"},
        )
    assert route.called
    request_body = route.calls[0].request.content
    assert b'"filter"' in request_body
    assert b'"chain"' in request_body
    assert b'"solana"' in request_body


@pytest.mark.asyncio
async def test_search_handles_http_500():
    """search() should handle HTTP errors gracefully."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock)
        respx_mock.post("/collections/default/points/search").respond(500)
        results = await store.search(query_embedding=[0.1] * 768, limit=5, min_similarity=0.5)
    assert results == []


@pytest.mark.asyncio
async def test_search_handles_network_error():
    """search() should handle network errors gracefully."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock)
        respx_mock.post("/collections/default/points/search").side_effect = Exception(
            "Connection refused"
        )
        results = await store.search(query_embedding=[0.1] * 768, limit=5, min_similarity=0.5)
    assert results == []


@pytest.mark.asyncio
async def test_search_with_custom_collection():
    """search() should use the specified collection name."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock, "scam_patterns")
        respx_mock.post("/collections/scam_patterns/points/search").respond(
            200, json={"result": []}
        )
        results = await store.search(
            query_embedding=[0.1] * 768,
            collection="scam_patterns",
            limit=3,
        )
    assert results == []


@pytest.mark.asyncio
async def test_search_uses_named_dense_vector_on_hybrid_collection():
    """search() against a hybrid (named-vector) collection must send the
    named dense vector, not a bare vector — Qdrant rejects bare vectors
    against named-vector collections with 400 "Not existing vector name"."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_hybrid_collection(respx_mock, "threat_intel")
        route = respx_mock.post("/collections/threat_intel/points/search").respond(
            200, json={"result": []}
        )
        await store.search(
            query_embedding=[0.1] * 768,
            collection="threat_intel",
            limit=5,
            min_similarity=0.4,
        )
    assert route.called
    request_body = route.calls[0].request.content
    assert b'"name":"dense"' in request_body
    assert b'"vector":' in request_body


@pytest.mark.asyncio
async def test_insert_stores_document():
    """insert() should PUT document to Qdrant and return True."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock, "scam_db")
        route = respx_mock.put("/collections/scam_db/points").respond(
            200, json={"result": {"status": "ok"}}
        )
        ok = await store.insert(
            doc_id="doc_001",
            collection="scam_db",
            embedding=[0.1] * 256,
            content="This is a scam pattern",
            metadata={"type": "honeypot"},
            source="manual",
            severity="critical",
            chain="ethereum",
        )
    assert ok is True
    assert route.called
    body = route.calls[0].request.content
    # insert() converts doc_id to a deterministic UUID v5 point id
    expected_point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "doc_001")).encode()
    assert expected_point_id in body
    assert b"scam pattern" in body
    assert b"critical" in body
    assert b"ethereum" in body


@pytest.mark.asyncio
async def test_insert_fails_on_http_error():
    """insert() should return False on HTTP error."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock, "test_coll")
        respx_mock.put("/collections/test_coll/points").respond(500)
        ok = await store.insert(
            doc_id="bad_doc",
            collection="test_coll",
            embedding=[0.1] * 256,
        )
    assert ok is False


@pytest.mark.asyncio
async def test_insert_creates_collection_if_missing():
    """insert() should create the collection if it doesn't exist."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock, "new_coll", exists=False)
        create_route = respx_mock.put("/collections/new_coll").respond(201, json={"result": True})
        respx_mock.put("/collections/new_coll/points").respond(
            200, json={"result": {"status": "ok"}}
        )
        ok = await store.insert(
            doc_id="new_doc",
            collection="new_coll",
            embedding=[0.1] * 256,
        )
    assert ok is True
    assert create_route.called


@pytest.mark.asyncio
async def test_hybrid_search():
    """hybrid_search() should include keyword matching in ranking."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock)
        respx_mock.post("/collections/default/points/search").respond(
            200,
            json={
                "result": [
                    {
                        "id": "doc_no_match",
                        "score": 0.9,
                        "payload": {
                            "content": "Bitcoin unrelated",
                            "metadata": {},
                            "source": "scan",
                            "severity": "low",
                        },
                    },
                    {
                        "id": "doc_keyword",
                        "score": 0.7,
                        "payload": {
                            "content": "Solana token with high risk",
                            "metadata": {},
                            "source": "scan",
                            "severity": "high",
                        },
                    },
                ]
            },
        )
        results = await store.hybrid_search(
            query_text="token risk solana",
            query_embedding=[0.1] * 768,
            limit=5,
        )
    assert len(results) >= 1
    # doc_keyword has keyword matches so should be ranked first
    assert results[0]["id"] == "doc_keyword"


@pytest.mark.asyncio
async def test_hybrid_search_fallback_to_vector():
    """Without query text, hybrid_search should fall back to vector search."""
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock)
        respx_mock.post("/collections/default/points/search").respond(
            200,
            json={
                "result": [
                    {
                        "id": "vec_doc",
                        "score": 0.75,
                        "payload": {
                            "content": "Some content",
                            "metadata": {},
                            "source": "test",
                            "severity": "medium",
                        },
                    }
                ]
            },
        )
        results = await store.hybrid_search(
            query_text="",
            query_embedding=[0.1] * 768,
            limit=5,
        )
    assert len(results) == 1
    assert results[0]["id"] == "vec_doc"


@pytest.mark.asyncio
async def test_get_qdrant_store_singleton():
    """get_qdrant_store() should return a singleton."""
    from rugbrain.qdrant_store import get_qdrant_store

    s1 = await get_qdrant_store()
    s2 = await get_qdrant_store()
    assert s1 is s2
    await s1.close()


def _mock_hybrid_collection(respx_mock, collection="default"):
    """Mock collection existence check with a BM25 sparse config."""
    respx_mock.get(f"/collections/{collection}").respond(
        200,
        json={"result": {"config": {"params": {"sparse_vectors": {"bm25": {}}}}}},
    )


@pytest.mark.asyncio
async def test_insert_includes_sparse_when_collection_supports(monkeypatch):
    """insert() should add a BM25 sparse vector for hybrid collections."""
    import rugbrain.qdrant_store as qdrant_store
    from rugbrain.qdrant_store import QdrantVectorStore

    async def _fake_embed_sparse(text):
        assert text == "solana honeypot rug"
        return ([10, 20, 30], [1.5, 0.8, 0.4])

    monkeypatch.setattr(qdrant_store, "_embed_sparse", _fake_embed_sparse)

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_hybrid_collection(respx_mock, "hybrid_coll")
        route = respx_mock.put("/collections/hybrid_coll/points").respond(
            200, json={"result": {"status": "ok"}}
        )
        ok = await store.insert(
            doc_id="doc_hybrid",
            collection="hybrid_coll",
            embedding=[0.1] * 1024,
            content="solana honeypot rug",
        )
    assert ok is True
    assert route.called
    body = route.calls[0].request.content
    assert b'"dense"' in body
    assert b'"bm25"' in body
    assert b'"indices"' in body
    assert b'"values"' in body
    assert b"10" in body and b"20" in body


@pytest.mark.asyncio
async def test_insert_dense_only_when_collection_has_no_sparse(monkeypatch):
    """insert() should keep dense-only payload for legacy collections."""
    import rugbrain.qdrant_store as qdrant_store
    from rugbrain.qdrant_store import QdrantVectorStore

    async def _fake_embed_sparse(text):
        raise AssertionError("sparse embedder must not be called for dense-only")

    monkeypatch.setattr(qdrant_store, "_embed_sparse", _fake_embed_sparse)

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock, "dense_only")
        route = respx_mock.put("/collections/dense_only/points").respond(
            200, json={"result": {"status": "ok"}}
        )
        ok = await store.insert(
            doc_id="doc_dense",
            collection="dense_only",
            embedding=[0.1] * 256,
            content="legacy content",
        )
    assert ok is True
    body = route.calls[0].request.content
    assert b'"bm25"' not in body
    assert b'"sparse"' not in body


@pytest.mark.asyncio
async def test_hybrid_search_native_rrf(monkeypatch):
    """hybrid_search() should use Qdrant's dense+sparse RRF fusion."""
    import rugbrain.qdrant_store as qdrant_store
    from rugbrain.qdrant_store import QdrantVectorStore

    async def _fake_embed_sparse(text):
        assert text == "solana rug"
        return ([5, 6], [0.9, 0.3])

    monkeypatch.setattr(qdrant_store, "_embed_sparse", _fake_embed_sparse)

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_hybrid_collection(respx_mock)
        route = respx_mock.post("/collections/default/points/search").respond(
            200,
            json={
                "result": [
                    {
                        "id": "hybrid_hit",
                        "score": 0.75,
                        "payload": {
                            "content": "Solana token rug",
                            "metadata": {},
                            "source": "scan",
                            "severity": "high",
                        },
                    }
                ]
            },
        )
        results = await store.hybrid_search(
            query_text="solana rug",
            query_embedding=[0.1] * 1024,
            limit=5,
        )
    assert route.called
    body = route.calls[0].request.content
    assert b'"sparse_vector"' in body
    assert b'"bm25"' in body
    assert b'"fusion"' in body
    assert b'"rrf"' in body
    assert b'"dense"' in body
    assert len(results) == 1
    assert results[0]["id"] == "hybrid_hit"
    assert results[0]["similarity"] == 0.75
    assert results[0]["content"] == "Solana token rug"
    assert results[0]["severity"] == "high"


@pytest.mark.asyncio
async def test_hybrid_search_falls_back_on_rrf_failure(monkeypatch):
    """hybrid_search() should fall back to dense search when RRF errors."""
    import rugbrain.qdrant_store as qdrant_store
    from rugbrain.qdrant_store import QdrantVectorStore

    async def _fake_embed_sparse(text):
        return ([1], [0.5])

    monkeypatch.setattr(qdrant_store, "_embed_sparse", _fake_embed_sparse)

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_hybrid_collection(respx_mock)
        respx_mock.post("/collections/default/points/search").respond(400)
        respx_mock.post("/collections/default/points/search").respond(
            200,
            json={
                "result": [
                    {
                        "id": "fallback_hit",
                        "score": 0.8,
                        "payload": {
                            "content": "Solana token risk",
                            "metadata": {},
                            "source": "scan",
                            "severity": "high",
                        },
                    }
                ]
            },
        )
        results = await store.hybrid_search(
            query_text="solana token",
            query_embedding=[0.1] * 1024,
            limit=5,
        )
    assert len(results) == 1
    assert results[0]["id"] == "fallback_hit"
    assert results[0]["similarity"] == 0.8


@pytest.mark.asyncio
async def test_hybrid_search_sparse_capability_cached(monkeypatch):
    """Sparse-capability probe should be cached per store instance."""
    import rugbrain.qdrant_store as qdrant_store
    from rugbrain.qdrant_store import QdrantVectorStore

    async def _fake_embed_sparse(text):
        return ([1], [0.5])

    monkeypatch.setattr(qdrant_store, "_embed_sparse", _fake_embed_sparse)

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        probe = respx_mock.get("/collections/default").respond(
            200,
            json={"result": {"config": {"params": {"sparse_vectors": {"bm25": {}}}}}},
        )
        respx_mock.post("/collections/default/points/search").respond(200, json={"result": []})
        await store.hybrid_search(query_text="q1", query_embedding=[0.1] * 1024)
        await store.hybrid_search(query_text="q2", query_embedding=[0.1] * 1024)
    assert probe.call_count == 1
