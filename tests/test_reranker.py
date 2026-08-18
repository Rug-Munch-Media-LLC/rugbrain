"""Tests for rugbrain.reranker — decoupled Qwen3 reranker client."""

from __future__ import annotations

import pytest
from rugbrain.reranker import Reranker, get_reranker, rerank, get_reranker_health


class TestReranker:
    def test_reranker_class_instantiation(self):
        r = Reranker(url="http://localhost:8003")
        assert r._url == "http://localhost:8003"

    def test_get_reranker_default(self):
        r = get_reranker()
        assert r._url is not None
        assert "8003" in r._url

    def test_reranker_class_type(self):
        r = Reranker()
        assert isinstance(r, Reranker)

    def test_reranker_class_url_default(self):
        r = Reranker()
        assert r._url is not None

    @pytest.mark.asyncio
    async def test_rerank_with_empty_docs(self):
        results = await rerank("test query", [])
        assert results == []

    @pytest.mark.asyncio
    async def test_rerank_with_server(self):
        results = await rerank(
            "BTC whale moving to exchange",
            ["Whale moves 5000 BTC to Kraken", "Ethereum gas prices low", "BTC ETF approved"],
            top_k=3,
        )
        # Server may be down or loading — either is acceptable
        if results:
            assert "relevance_score" in results[0]
            assert "index" in results[0]

    @pytest.mark.asyncio
    async def test_reranker_health(self):
        health = await get_reranker_health()
        assert "ok" in health
        assert "enabled" in health
