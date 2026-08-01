"""Tests for the BGE cross-encoder reranker (bge-reranker-v2-m3)."""

import asyncio

import pytest


@pytest.fixture
def event_loop():
    """Explicit event loop fixture for pytest-asyncio auto mode."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sample_docs():
    return [
        {
            "id": "1",
            "content": "The DAO hack used a reentrancy attack to drain 3.6M ETH",
            "score": 0.7,
        },
        {"id": "2", "content": "Bitcoin price reaches new all-time high", "score": 0.5},
        {
            "id": "3",
            "content": "Reentrancy exploits call external contracts before state updates",
            "score": 0.3,
        },
    ]


def test_reranker_module_imports():
    """The reranker module must import cleanly (no hard deps at import time)."""
    import rmi_rag.reranker  # noqa: F401


def test_rerank_returns_top_k_sorted():
    """ST backend: returns top_k docs sorted by cross score, with metadata keys."""
    from rmi_rag.reranker import Reranker

    reranker = Reranker(use_ollama=False)
    reranker._st_loaded = True
    # fake cross-encoder: reentrancy-related docs score higher
    fake_scores = [0.9, 0.1, 0.8]

    def fake_score(query, docs):
        return fake_scores

    reranker._score_st = fake_score

    async def run():
        return await reranker.rerank(
            "reentrancy vulnerability",
            [
                {
                    "id": "1",
                    "content": "The DAO hack used a reentrancy attack to drain 3.6M ETH",
                    "score": 0.7,
                },
                {"id": "2", "content": "Bitcoin price reaches new all-time high", "score": 0.5},
                {
                    "id": "3",
                    "content": "Reentrancy exploits call external contracts before state updates",
                    "score": 0.3,
                },
            ],
            top_k=2,
        )

    results = asyncio.run(run())

    assert len(results) == 2
    # Doc 1 (highest fake score) must rank first
    assert results[0]["id"] == "1"
    assert results[0]["cross_score"] == pytest.approx(0.9, abs=1e-6)
    assert results[0]["rerank_method"] == "sentence-transformers"
    assert "combined_score" in results[0]
    assert "rerank_duration_ms" in results[0]


def test_rerank_graceful_no_backend():
    """No backend available: returns docs sorted by original score, never raises."""
    from rmi_rag.reranker import Reranker

    reranker = Reranker(use_ollama=False)
    reranker._st_load_failed = True
    reranker._st_loaded = False

    async def run():
        return await reranker.rerank(
            "reentrancy vulnerability",
            [
                {"id": "1", "content": "The DAO hack used a reentrancy attack", "score": 0.7},
                {"id": "2", "content": "Bitcoin price reaches new all-time high", "score": 0.5},
            ],
            top_k=2,
        )

    results = asyncio.run(run())

    assert len(results) == 2
    assert results[0]["id"] == "1"  # highest original score first
    assert results[0]["rerank_method"] == "none"


def test_rerank_empty_documents():
    """Empty candidate list returns empty list."""
    from rmi_rag.reranker import Reranker

    reranker = Reranker(use_ollama=False)

    async def run():
        return await reranker.rerank("anything", [], top_k=5)

    assert asyncio.run(run()) == []


def test_ollama_backend_preferred_when_available():
    """When Ollama has a reranker model, /api/rerank is used."""
    from rmi_rag.reranker import Reranker

    reranker = Reranker(use_ollama=True)

    async def fake_has():
        return True

    async def fake_score(query, docs):
        return [0.9, 0.1]

    reranker._ollama_has_reranker = fake_has
    reranker._score_ollama = fake_score

    async def run():
        return await reranker.rerank(
            "reentrancy vulnerability",
            [
                {"id": "1", "content": "The DAO hack used a reentrancy attack", "score": 0.7},
                {"id": "2", "content": "Bitcoin price reaches new all-time high", "score": 0.5},
            ],
            top_k=2,
        )

    results = asyncio.run(run())

    assert len(results) == 2
    assert results[0]["id"] == "1"
    assert results[0]["rerank_method"] == "ollama-bge-reranker-v2-m3"


def test_ollama_failure_falls_back_to_st():
    """Ollama /api/rerank failure → sentence-transformers fallback."""
    from rmi_rag.reranker import Reranker

    reranker = Reranker(use_ollama=True)
    reranker._st_loaded = True

    async def fake_has():
        return True

    async def fake_ollama_fail(query, docs):
        raise RuntimeError("ollama rerank unavailable")

    def fake_st(query, docs):
        return [0.9, 0.1]

    reranker._ollama_has_reranker = fake_has
    reranker._score_ollama = fake_ollama_fail
    reranker._score_st = fake_st

    async def run():
        return await reranker.rerank(
            "reentrancy vulnerability",
            [
                {"id": "1", "content": "The DAO hack used a reentrancy attack", "score": 0.7},
                {"id": "2", "content": "Bitcoin price reaches new all-time high", "score": 0.5},
            ],
            top_k=2,
        )

    results = asyncio.run(run())

    assert len(results) == 2
    assert results[0]["rerank_method"] == "sentence-transformers"


def test_health_check_shape():
    """health_check returns backend state metadata."""
    from rmi_rag.reranker import Reranker

    reranker = Reranker(use_ollama=False)
    hc = reranker.health_check()

    assert "backend" in hc
    assert "st_model" in hc
    assert "st_loaded" in hc
    assert "load_time_s" in hc


def test_singleton():
    """get_reranker returns the same instance."""
    from rmi_rag.reranker import get_reranker

    async def run():
        a = await get_reranker()
        b = await get_reranker()
        return a, b

    a, b = asyncio.run(run())
    assert a is b


def test_search_endpoint_signature_has_rerank_param():
    """/api/v1/search accepts the new rerank query param."""
    import inspect

    from rmi_rag.endpoints import search_threat_intel

    sig = inspect.signature(search_threat_intel)
    assert "rerank" in sig.parameters
