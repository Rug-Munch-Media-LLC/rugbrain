"""Mocked core-RAG tests — embedding instruction prefix, rerank, hybrid RRF,
qdrant_store insert/query.

Everything is mocked with respx / monkeypatch: NO live Qdrant (:6333/:6334),
NO live embedder/reranker traffic. Safe to run while the re-embedding
migration is in progress.
"""

from __future__ import annotations

import json
import math
import uuid
from typing import Any

import pytest
import respx

import rugbrain.embeddings as embeddings_mod
import rugbrain.reranker as reranker_mod
from rugbrain.rrf_fusion import _rrf_fuse

# ── 1. Embedding instruction-prefix ──────────────────────────────────


@pytest.fixture()
def _fresh_embedder_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the per-process reachability cache so every test re-probes."""
    monkeypatch.setenv("RAG_EMBED_NO_CACHE", "1")
    monkeypatch.delenv("_QWEN_PROBED", raising=False)
    monkeypatch.delenv("_QWEN_PROBED_AT", raising=False)
    monkeypatch.delenv("_QWEN_OK", raising=False)


def _embed_response(dim: int | None = None) -> dict[str, Any]:
    # Full-dim vector: _resize() REJECTS dim mismatches since T04/TOP-100#25
    # (silent pad/truncate poisoned retrieval spaces). It L2-normalizes only.
    d = dim if dim is not None else embeddings_mod.EMBEDDING_DIM
    return {"data": [{"index": 0, "embedding": [0.5] * d}]}


@pytest.mark.asyncio
async def test_query_embedding_gets_instruct_prefix(
    monkeypatch: pytest.MonkeyPatch, _fresh_embedder_probe: Any
) -> None:
    """query=True must send the Qwen3 Instruct template, not raw text."""
    monkeypatch.setattr(embeddings_mod, "_BACKEND", "int")
    base = embeddings_mod.QWEN_EMBED_URL

    with respx.mock(base_url=base, assert_all_called=False) as rx:
        rx.get("/health").respond(200, json={"status": "ok"})
        route = rx.post("/embeddings").respond(200, json=_embed_response())

        vec = await embeddings_mod.get_embedding("is this token a rug pull", query=True)

    assert route.called
    payload = json.loads(route.calls[0].request.content)
    sent_text = payload["input"][0]
    assert sent_text.startswith("Instruct: Given a web search query")
    assert sent_text.endswith("Query: is this token a rug pull")
    # padded to the canonical dim and L2-normalized
    assert len(vec) == embeddings_mod.EMBEDDING_DIM
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_document_embedding_stays_raw(
    monkeypatch: pytest.MonkeyPatch, _fresh_embedder_probe: Any
) -> None:
    """Documents must NOT carry the Instruct prefix (query-only template)."""
    monkeypatch.setattr(embeddings_mod, "_BACKEND", "int")
    base = embeddings_mod.QWEN_EMBED_URL

    with respx.mock(base_url=base, assert_all_called=False) as rx:
        rx.get("/health").respond(200, json={"status": "ok"})
        route = rx.post("/embeddings").respond(200, json=_embed_response())

        await embeddings_mod.get_embedding("honeypot contract analysis", query=False)

    payload = json.loads(route.calls[0].request.content)
    assert payload["input"][0] == "honeypot contract analysis"


@pytest.mark.asyncio
async def test_get_query_embedding_helper_uses_prefix(
    monkeypatch: pytest.MonkeyPatch, _fresh_embedder_probe: Any
) -> None:
    monkeypatch.setattr(embeddings_mod, "_BACKEND", "int")
    base = embeddings_mod.QWEN_EMBED_URL

    with respx.mock(base_url=base, assert_all_called=False) as rx:
        rx.get("/health").respond(200, json={"status": "ok"})
        route = rx.post("/embeddings").respond(200, json=_embed_response())

        await embeddings_mod.get_query_embedding("flash loan attack")

    payload = json.loads(route.calls[0].request.content)
    assert payload["input"][0].startswith("Instruct: ")


@pytest.mark.asyncio
async def test_embedding_fails_loud_when_backends_down(
    monkeypatch: pytest.MonkeyPatch, _fresh_embedder_probe: Any
) -> None:
    """T04 contract: no silent hash fallback — raise RAGUnavailableError."""
    monkeypatch.setattr(embeddings_mod, "_BACKEND", "auto")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    base = embeddings_mod.QWEN_EMBED_URL

    with respx.mock(base_url=base, assert_all_called=False) as rx:
        rx.get("/health").respond(500)
        with pytest.raises(embeddings_mod.RAGUnavailableError):
            await embeddings_mod.get_embedding("anything", query=True)


# ── 2. Rerank ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_sorts_and_truncates(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def _ok() -> bool:
        return True

    monkeypatch.setattr(reranker_mod, "_fast_health", _ok)
    base = reranker_mod.RERANKER_URL

    with respx.mock(base_url=base, assert_all_called=False) as rx:
        route = rx.post("/rerank").respond(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.10},
                    {"index": 1, "relevance_score": 0.95},
                    {"index": 2, "relevance_score": 0.55},
                ]
            },
        )
        out = await reranker_mod.rerank("rug pull", ["a", "b", "c"], top_k=2)

    assert route.called
    sent = json.loads(route.calls[0].request.content)
    assert sent["query"] == "rug pull"
    assert sent["documents"] == ["a", "b", "c"]
    assert sent["top_n"] == 2
    # sorted descending, truncated to top_k
    assert [r["index"] for r in out] == [1, 2]
    assert out[0]["relevance_score"] == 0.95


@pytest.mark.asyncio
async def test_rerank_raw_attaches_documents(monkeypatch: pytest.MonkeyPatch) -> Any:
    async def _ok() -> bool:
        return True

    monkeypatch.setattr(reranker_mod, "_fast_health", _ok)
    base = reranker_mod.RERANKER_URL

    with respx.mock(base_url=base, assert_all_called=False) as rx:
        route = rx.post("/rerank").respond(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.2},
                ]
            },
        )
        out = await reranker_mod.rerank("q", ["doc-zero", "doc-one"], top_k=5, raw=True)

    sent = json.loads(route.calls[0].request.content)
    assert sent["top_n"] == 2  # raw=True asks for every doc
    assert out[0]["document"] == "doc-one"
    assert out[1]["document"] == "doc-zero"


@pytest.mark.asyncio
async def test_rerank_degrades_gracefully_when_down(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A transport failure on the hosted lane fails open to [] (never raises).

    The local :8003 lane and its ``_fast_health`` probe were retired
    2026-10-11; ``rerank`` now POSTs the hosted chain directly and returns []
    on any error. Force the error with respx so no live traffic is attempted.
    """
    import httpx

    with respx.mock(assert_all_called=False) as rx:
        rx.post(url__regex=r".*/rerank").mock(side_effect=httpx.ConnectError("down"))
        out = await reranker_mod.rerank("q", ["a", "b"])
    assert out == []


@pytest.mark.asyncio
async def test_rerank_disabled_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reranker_mod, "RERANKER_ENABLED", False)
    out = await reranker_mod.rerank("q", ["a"])
    assert out == []


# ── 3. Hybrid RRF fusion ─────────────────────────────────────────────


def _doc(doc_id: str, **extra: Any) -> dict[str, Any]:
    return {"doc_id": doc_id, "content": f"content-{doc_id}", **extra}


def test_rrf_doc_in_both_lists_outranks_single_list_winner() -> None:
    dense = [_doc("a"), _doc("b"), _doc("c")]
    sparse = [_doc("b"), _doc("d")]
    fused = _rrf_fuse([dense, sparse], k=60)
    # 'b' appears in both lists -> highest fused score
    assert fused[0]["doc_id"] == "b"
    expected_b = 1 / (60 + 2) + 1 / (60 + 1)
    assert abs(fused[0]["rrf_score"] - round(expected_b, 6)) < 1e-9


def test_rrf_entity_boost_changes_order() -> None:
    dense = [_doc("a"), _doc("b")]
    entity = [_doc("x"), _doc("y")]
    plain = _rrf_fuse([dense, entity], k=60)
    boosted = _rrf_fuse([dense, entity], k=60, entity_doc_ids={"y"})
    plain_order = [d["doc_id"] for d in plain]
    boosted_order = [d["doc_id"] for d in boosted]
    # 'y' (rank 2 of list 2) overtakes everything except dense rank-1 'a'
    assert plain_order.index("y") > boosted_order.index("y")
    y = next(d for d in boosted if d["doc_id"] == "y")
    assert y["entity_match"] is True
    assert y["rrf_score"] > (1 / (60 + 2)) * 1.5 - 1e-9


def test_rrf_pillar_weights_respected() -> None:
    dense = [_doc("a")]
    sparse = [_doc("b")]
    fused = _rrf_fuse([dense, sparse], k=60, pillar_weights=[0.0, 1.0])
    assert fused[0]["doc_id"] == "b"
    assert fused[0]["rrf_score"] == round(1 / 61, 6)
    # zero-weighted list contributes nothing
    assert next(d for d in fused if d["doc_id"] == "a")["rrf_score"] == 0.0


def test_rrf_skips_docs_without_id() -> None:
    fused = _rrf_fuse([[{"content": "no id"}, _doc("a")]], k=60)
    assert [d["doc_id"] for d in fused] == ["a"]


# ── 4. qdrant_store insert/query (mocked) ────────────────────────────

MOCK_QDRANT = "http://mock-qdrant-test:6333"


def _mock_collection(rx: Any, collection: str, exists: bool = True) -> None:
    status = 200 if exists else 404
    rx.get(f"/collections/{collection}").respond(
        status, json={"status": "ok" if exists else "not found"}
    )


@pytest.mark.asyncio
async def test_qdrant_insert_posts_point(monkeypatch: pytest.MonkeyPatch) -> None:
    from rugbrain.qdrant_store import QdrantVectorStore

    monkeypatch.setattr("rugbrain.qdrant_store._RMI_RAG_QDRANT_WRITE_MODE", "writable")
    store = QdrantVectorStore(url=MOCK_QDRANT)

    with respx.mock(base_url=MOCK_QDRANT, assert_all_called=False) as rx:
        _mock_collection(rx, "scam_intel")
        route = rx.put("/collections/scam_intel/points").respond(
            200, json={"result": {"status": "ok"}}
        )
        ok = await store.insert(
            doc_id="pt-1",
            collection="scam_intel",
            embedding=[0.25] * 32,
            content="deployer funded by tornado cash",
            metadata={"kind": "signal"},
            source="scanner",
            severity="high",
            chain="ethereum",
        )

    assert ok is True
    assert route.called
    body = json.loads(route.calls[0].request.content)
    point = body["points"][0]
    assert point["id"] == str(uuid.uuid5(uuid.NAMESPACE_DNS, "pt-1"))
    assert point["vector"] == [0.25] * 32
    assert point["payload"]["content"] == "deployer funded by tornado cash"
    assert point["payload"]["source"] == "scanner"
    assert point["payload"]["severity"] == "high"
    assert point["payload"]["metadata"]["kind"] == "signal"


@pytest.mark.asyncio
async def test_qdrant_insert_readonly_mode_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read-only deployments must refuse writes instead of failing mid-flight."""
    from rugbrain.qdrant_store import QdrantVectorStore

    monkeypatch.setattr("rugbrain.qdrant_store._RMI_RAG_QDRANT_WRITE_MODE", "readonly")
    store = QdrantVectorStore(url=MOCK_QDRANT)

    with respx.mock(base_url=MOCK_QDRANT, assert_all_called=False) as rx:
        _mock_collection(rx, "scam_intel")
        route = rx.put("/collections/scam_intel/points").respond(200, json={"result": {}})
        ok = await store.insert(doc_id="pt-2", collection="scam_intel", embedding=[0.1] * 8)

    assert ok is False
    assert not route.called


@pytest.mark.asyncio
async def test_qdrant_search_maps_results() -> None:
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url=MOCK_QDRANT)
    with respx.mock(base_url=MOCK_QDRANT, assert_all_called=False) as rx:
        _mock_collection(rx, "threat_intel")
        rx.post("/collections/threat_intel/points/search").respond(
            200,
            json={
                "result": [
                    {
                        "id": "x1",
                        "score": 0.91,
                        "payload": {
                            "content": "rug pull checklist",
                            "metadata": {"collection": "threat_intel"},
                            "source": "news",
                            "severity": "medium",
                        },
                    }
                ]
            },
        )
        results = await store.search(query_embedding=[0.1] * 32, collection="threat_intel")

    assert len(results) == 1
    assert results[0]["id"] == "x1"
    assert results[0]["similarity"] == 0.91
    assert results[0]["content"] == "rug pull checklist"
    assert results[0]["source"] == "news"


@pytest.mark.asyncio
async def test_qdrant_search_empty_on_http_error() -> None:
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url=MOCK_QDRANT)
    with respx.mock(base_url=MOCK_QDRANT, assert_all_called=False) as rx:
        _mock_collection(rx, "threat_intel")
        rx.post("/collections/threat_intel/points/search").respond(500)
        results = await store.search(query_embedding=[0.1] * 32, collection="threat_intel")
    assert results == []


@pytest.mark.asyncio
async def test_qdrant_has_point() -> None:
    from rugbrain.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url=MOCK_QDRANT)
    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "dup-1"))
    with respx.mock(base_url=MOCK_QDRANT, assert_all_called=False) as rx:
        rx.get(f"/collections/scam_intel/points/{point_id}").respond(
            200, json={"result": {"id": point_id}}
        )
        assert await store.has_point("dup-1", "scam_intel") is True
        missing_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "missing"))
        rx.get(f"/collections/scam_intel/points/{missing_id}").respond(404)
        assert await store.has_point("missing", "scam_intel") is False


def test_build_qdrant_filter_shapes() -> None:
    from rugbrain.qdrant_store import build_qdrant_filter

    assert build_qdrant_filter() is None
    assert build_qdrant_filter(match={"severity": ""}) is None

    f = build_qdrant_filter(
        match={"severity": "critical", "chain": "solana"},
        ranges=[("amount_lost_usd", {"gte": 1000.0})],
    )
    assert f is not None
    keys = [m["key"] for m in f["must"]]
    assert "severity" in keys and "chain" in keys and "amount_lost_usd" in keys
    rng = next(m for m in f["must"] if m["key"] == "amount_lost_usd")
    assert rng["range"] == {"gte": 1000.0}
