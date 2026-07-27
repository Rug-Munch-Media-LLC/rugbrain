"""
Tests for qdrant_store.py — Qdrant vector store operations.

Uses respx to mock HTTP calls to the Qdrant REST API.
"""

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
    from rmi_rag.qdrant_store import QdrantVectorStore

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
    from rmi_rag.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock)
        _mock_search(respx_mock, [])
        results = await store.search(query_embedding=[0.1] * 768, limit=5, min_similarity=0.5)
    assert results == []


@pytest.mark.asyncio
async def test_search_with_filters():
    """search() should pass filters to Qdrant."""
    from rmi_rag.qdrant_store import QdrantVectorStore

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
    from rmi_rag.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_collection(respx_mock)
        respx_mock.post("/collections/default/points/search").respond(500)
        results = await store.search(query_embedding=[0.1] * 768, limit=5, min_similarity=0.5)
    assert results == []


@pytest.mark.asyncio
async def test_search_handles_network_error():
    """search() should handle network errors gracefully."""
    from rmi_rag.qdrant_store import QdrantVectorStore

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
    from rmi_rag.qdrant_store import QdrantVectorStore

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
async def test_insert_stores_document():
    """insert() should PUT document to Qdrant and return True."""
    from rmi_rag.qdrant_store import QdrantVectorStore

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
    assert b"doc_001" in body
    assert b"scam pattern" in body
    assert b"critical" in body
    assert b"ethereum" in body


@pytest.mark.asyncio
async def test_insert_fails_on_http_error():
    """insert() should return False on HTTP error."""
    from rmi_rag.qdrant_store import QdrantVectorStore

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
    from rmi_rag.qdrant_store import QdrantVectorStore

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
    from rmi_rag.qdrant_store import QdrantVectorStore

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
    from rmi_rag.qdrant_store import QdrantVectorStore

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
    from rmi_rag.qdrant_store import get_qdrant_store

    s1 = await get_qdrant_store()
    s2 = await get_qdrant_store()
    assert s1 is s2
    await s1.close()
