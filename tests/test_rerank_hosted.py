"""Hosted rerank rung tests — DeepInfra shape, ladder order, fail-open.

Hermetic: fake HTTP clients and monkeypatched rungs; no network.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

import rugbrain.rerank_hosted as hosted
import rugbrain.search.rerank as sr


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict[str, Any] | None = None, exc: Exception | None = None) -> None:
        self._payload = payload or {}
        self._exc = exc

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(
        self, url: str, headers: dict | None = None, json: dict | None = None
    ) -> _FakeResponse:
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._payload)


def _pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sr, "RERANKER_ENABLED", True)
    monkeypatch.setattr(sr, "hosted_enabled", lambda: True)


def _chunks(n: int) -> list[dict[str, Any]]:
    return [{"content": f"doc{i}", "score": 1.0 - i * 0.01} for i in range(n)]


# ── hosted module ────────────────────────────────────────────────────────


def test_hosted_enabled_key_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RERANK_HOSTED", "1")
    monkeypatch.setattr(hosted, "_deepinfra_key", lambda: "")
    assert hosted.hosted_enabled() is False
    monkeypatch.setattr(hosted, "_deepinfra_key", lambda: "k" * 32)
    assert hosted.hosted_enabled() is True
    monkeypatch.setenv("RERANK_HOSTED", "0")
    assert hosted.hosted_enabled() is False


def test_hosted_rerank_scores_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hosted, "hosted_enabled", lambda: True)
    monkeypatch.setattr(hosted, "_deepinfra_key", lambda: "k" * 32)
    client = _FakeClient({"scores": [0.1, 0.9, 0.5]})
    monkeypatch.setattr(hosted.httpx, "AsyncClient", lambda timeout=None: client)

    out = asyncio.run(hosted.hosted_rerank("q", ["a", "b", "c"]))
    assert out is not None
    assert [r["index"] for r in out] == [1, 2, 0]
    assert out[0]["relevance_score"] == pytest.approx(0.9)

    top = asyncio.run(hosted.hosted_rerank("q", ["a", "b", "c"], top_n=2))
    assert top is not None and len(top) == 2


def test_hosted_rerank_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hosted, "hosted_enabled", lambda: True)
    monkeypatch.setattr(hosted, "_deepinfra_key", lambda: "k" * 32)
    client = _FakeClient(exc=httpx.ConnectError("down"))
    monkeypatch.setattr(hosted.httpx, "AsyncClient", lambda timeout=None: client)

    assert asyncio.run(hosted.hosted_rerank("q", ["a"])) is None


def test_hosted_rerank_shape_mismatch_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hosted, "hosted_enabled", lambda: True)
    monkeypatch.setattr(hosted, "_deepinfra_key", lambda: "k" * 32)
    client = _FakeClient({"scores": [0.5]})
    monkeypatch.setattr(hosted.httpx, "AsyncClient", lambda timeout=None: client)

    assert asyncio.run(hosted.hosted_rerank("q", ["a", "b"])) is None


# ── ladder wiring in search/rerank ───────────────────────────────────────


def test_live_tier_prefers_hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(monkeypatch)
    local_calls = {"n": 0}

    async def fake_hosted(query: str, docs: list[str], top_n: int = 0, **kw: Any):
        return [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}]

    async def fake_post(url: str, query: str, docs: list[str], timeout: float, label: str):
        local_calls["n"] += 1
        return None

    monkeypatch.setattr(sr, "hosted_rerank", fake_hosted)
    monkeypatch.setattr(sr, "_post_rerank", fake_post)

    scores, method = asyncio.run(sr._score_candidates("q", _chunks(2), tier="live"))
    assert method == "deepinfra:qwen3-reranker-4b"
    assert scores[1] == 0.9
    assert scores[0] == 0.2
    assert local_calls["n"] == 0


def test_live_tier_falls_to_local_on_hosted_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(monkeypatch)

    async def fake_hosted(query: str, docs: list[str], top_n: int = 0, **kw: Any):
        return None

    async def fake_post(url: str, query: str, docs: list[str], timeout: float, label: str):
        if url == sr.RERANKER_URL:
            return [{"index": 0, "relevance_score": 0.7}]
        return None  # retired :8008 speed tier

    monkeypatch.setattr(sr, "hosted_rerank", fake_hosted)
    monkeypatch.setattr(sr, "_post_rerank", fake_post)

    scores, method = asyncio.run(sr._score_candidates("q", _chunks(2), tier="live"))
    assert method == "qwen3-reranker-4b"
    assert scores[0] == 0.7


def test_deep_tier_local_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(monkeypatch)
    hosted_calls = {"n": 0}

    async def fake_hosted(query: str, docs: list[str], top_n: int = 0, **kw: Any):
        hosted_calls["n"] += 1
        return [{"index": 0, "relevance_score": 0.99}]

    async def fake_post(url: str, query: str, docs: list[str], timeout: float, label: str):
        return [{"index": 0, "relevance_score": 0.8}]

    monkeypatch.setattr(sr, "hosted_rerank", fake_hosted)
    monkeypatch.setattr(sr, "_post_rerank", fake_post)

    scores, method = asyncio.run(sr._score_candidates("q", _chunks(2), tier="deep"))
    assert method == "qwen3-reranker-4b"
    assert scores[0] == 0.8
    assert hosted_calls["n"] == 0


def test_deep_tier_hosted_fallback_on_local_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(monkeypatch)

    async def fake_hosted(query: str, docs: list[str], top_n: int = 0, **kw: Any):
        return [{"index": 1, "relevance_score": 0.95}]

    async def fake_post(url: str, query: str, docs: list[str], timeout: float, label: str):
        return None

    monkeypatch.setattr(sr, "hosted_rerank", fake_hosted)
    monkeypatch.setattr(sr, "_post_rerank", fake_post)

    scores, method = asyncio.run(sr._score_candidates("q", _chunks(2), tier="deep"))
    assert method == "deepinfra:qwen3-reranker-4b"
    assert scores[1] == 0.95
