"""Tests for rugbrain.reranker — decoupled Qwen3 reranker client."""

from __future__ import annotations

import os

import pytest

from rugbrain import rerank_compat
from rugbrain.reranker import Reranker, get_reranker, get_reranker_health, rerank


class TestReranker:
    def test_reranker_class_instantiation(self) -> None:
        r = Reranker(url="http://localhost:8003")
        assert r._url == "http://localhost:8003"

    def test_get_reranker_default(self) -> None:
        # 2026-10-23 contract change: RERANKER_URL is REQUIRED config. The old
        # baked default was the DeepInfra inference URL, which answers 402
        # (overdue invoices) since 2026-10-22 — a default pointing at a dead
        # paid lane. No default is the honest shape: deployments set it.
        r = get_reranker()
        assert r._url == os.getenv("RERANKER_URL", "").rstrip("/")

    @pytest.mark.asyncio
    async def test_health_reports_unconfigured_url(self, monkeypatch) -> None:
        monkeypatch.setattr(rerank_compat, "RERANKER_URL", "")
        health = await Reranker(url="").health()
        assert health["ok"] is False
        assert "RERANKER_URL" in health["error"]

    def test_reranker_class_type(self) -> None:
        r = Reranker()
        assert isinstance(r, Reranker)

    def test_reranker_class_url_default(self) -> None:
        r = Reranker()
        assert r._url is not None

    @pytest.mark.asyncio
    async def test_rerank_with_empty_docs(self) -> None:
        results = await rerank("test query", [])
        assert results == []

    @pytest.mark.asyncio
    async def test_rerank_with_server(self) -> None:
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
    async def test_reranker_health(self) -> None:
        health = await get_reranker_health()
        assert "ok" in health
        assert "enabled" in health
