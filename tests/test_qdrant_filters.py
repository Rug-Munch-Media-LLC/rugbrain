"""Tests for structured payload filters in qdrant_store (hybrid search).

Covers the pure filter builders plus end-to-end filter pass-through into the
Qdrant search payloads (RRF and dense fallback), using respx mocks.
"""

import pytest
import respx

from rmi_rag.qdrant_store import build_qdrant_filter, build_search_filters


class TestBuildQdrantFilter:
    def test_empty_returns_none(self):
        assert build_qdrant_filter() is None
        assert build_qdrant_filter({}, []) is None

    def test_single_match(self):
        result = build_qdrant_filter({"severity": "critical"})
        assert result == {"must": [{"key": "severity", "match": {"value": "critical"}}]}

    def test_match_skips_empty(self):
        result = build_qdrant_filter({"severity": "", "chain": "ethereum"})
        assert result == {"must": [{"key": "chain", "match": {"value": "ethereum"}}]}

    def test_range(self):
        result = build_qdrant_filter(None, [("date_ts", {"gte": 1672531200})])
        assert result == {"must": [{"key": "date_ts", "range": {"gte": 1672531200}}]}

    def test_match_and_range(self):
        result = build_qdrant_filter(
            {"chain": "ethereum"}, [("amount_lost_usd", {"gte": 1_000_000})]
        )
        assert result is not None
        assert len(result["must"]) == 2

    def test_range_skips_empty_constraint(self):
        assert build_qdrant_filter(None, [("date_ts", {})]) is None
        assert build_qdrant_filter(None, [("date_ts", {"gte": None})]) is None


class TestBuildSearchFilters:
    def test_none_when_no_params(self):
        assert build_search_filters() is None

    def test_severity_normalized(self):
        result = build_search_filters(severity="CRITICAL")
        assert result is not None
        assert result["match"]["severity"] == "critical"

    def test_chain_lowercased(self):
        result = build_search_filters(chain="Ethereum")
        assert result is not None
        assert result["match"]["chain"] == "ethereum"

    def test_source_type_lowercased(self):
        result = build_search_filters(source_type="REKT_NEWS")
        assert result is not None
        assert result["match"]["source_type"] == "rekt_news"

    def test_date_after_converts_to_ts(self):
        result = build_search_filters(date_after="2023-01-01")
        assert result is not None
        ranges = dict(result["range"])
        assert "date_ts" in ranges
        assert ranges["date_ts"]["gte"] == 1672531200.0

    def test_amount_min(self):
        result = build_search_filters(amount_min=5_000_000)
        assert result is not None
        ranges = dict(result["range"])
        assert ranges["amount_lost_usd"]["gte"] == 5_000_000.0

    def test_full_filter(self):
        result = build_search_filters(
            severity="critical",
            chain="ethereum",
            attack_vector="reentrancy",
            date_after="2023-01-01",
            amount_min=1_000_000,
        )
        assert result is not None
        assert result["match"]["severity"] == "critical"
        assert result["match"]["attack_vector"] == "reentrancy"
        assert len(result["range"]) == 2


# ── Filter pass-through into live search payloads ────────────────────


def _mock_hybrid_collection(respx_mock, collection="default"):
    respx_mock.get(f"/collections/{collection}").respond(
        200,
        json={"result": {"config": {"params": {"sparse_vectors": {"bm25": {}}}}}},
    )


@pytest.mark.asyncio
async def test_hybrid_search_rrf_sends_filter(monkeypatch):
    """hybrid_search() must include the structured filter in the RRF request."""
    import rmi_rag.qdrant_store as qdrant_store
    from rmi_rag.qdrant_store import QdrantVectorStore

    async def _fake_embed_sparse(text):
        return ([5, 6], [0.9, 0.3])

    monkeypatch.setattr(qdrant_store, "_embed_sparse", _fake_embed_sparse)

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        _mock_hybrid_collection(respx_mock, "threat_intel")
        route = respx_mock.post("/collections/threat_intel/points/search").respond(
            200, json={"result": []}
        )
        filters = build_search_filters(
            severity="critical", chain="ethereum", date_after="2023-01-01"
        )
        await store.hybrid_search(
            query_text="reentrancy",
            query_embedding=[0.1] * 1024,
            collection="threat_intel",
            limit=5,
            filters=filters,
        )
    assert route.called
    body = route.calls[0].request.content
    assert b'"filter"' in body
    assert b'"severity"' in body
    assert b'"critical"' in body
    assert b'"date_ts"' in body
    assert b"1672531200" in body


@pytest.mark.asyncio
async def test_hybrid_search_dense_fallback_sends_filter():
    """hybrid_search() must forward filters to the dense fallback path."""
    from rmi_rag.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        # dense-only collection: no sparse_vectors in config
        respx_mock.get("/collections/hacks").respond(
            200, json={"result": {"config": {"params": {}}}}
        )
        route = respx_mock.post("/collections/hacks/points/search").respond(
            200, json={"result": []}
        )
        filters = build_search_filters(attack_vector="flash-loan", amount_min=1_000_000)
        await store.hybrid_search(
            query_text="flash loan",
            query_embedding=[0.1] * 1024,
            collection="hacks",
            limit=5,
            filters=filters,
        )
    assert route.called
    body = route.calls[0].request.content
    assert b'"filter"' in body
    assert b'"flash-loan"' in body
    assert b'"amount_lost_usd"' in body
    assert b"1000000" in body


@pytest.mark.asyncio
async def test_hybrid_search_no_filter_omits_filter_key():
    """hybrid_search() must omit the filter key when no filters are given."""
    from rmi_rag.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://rmi-qdrant:6333")
    with respx.mock(base_url="http://rmi-qdrant:6333", assert_all_called=False) as respx_mock:
        respx_mock.get("/collections/hacks").respond(
            200, json={"result": {"config": {"params": {}}}}
        )
        route = respx_mock.post("/collections/hacks/points/search").respond(
            200, json={"result": []}
        )
        await store.hybrid_search(
            query_text="flash loan",
            query_embedding=[0.1] * 1024,
            collection="hacks",
            limit=5,
        )
    assert route.called
    body = route.calls[0].request.content
    assert b'"filter"' not in body


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
