"""Dual-tier retriever tests — routing, fail-open, instruction propagation.

All network and Qdrant interactions are mocked; no live services required.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

importlib.import_module("rugbrain.search.rerank")  # ensure instruction lib loads first
dual = importlib.import_module("rugbrain.search.dual_tier")


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"http {self.status}")

    async def json(self):
        return self._payload


class FakeSession:
    """Routes fake HTTP by URL substring."""

    def __init__(self, routes: dict[str, dict[str, Any]], fail: set[str] | None = None):
        self.routes = routes
        self.fail = fail or set()
        self.calls: list[str] = []

    def post(self, url: str, json=None, **kw):
        self.calls.append(url)
        for key, payload in self.routes.items():
            if key in url:
                if key in self.fail:
                    raise aiohttp_fail()
                return FakeResponse(payload)
        raise AssertionError(f"no route for {url}")

    async def close(self):
        pass


def aiohttp_fail():
    import aiohttp

    return aiohttp.ClientError("simulated outage")


def fake_qdrant(points: list[Any]):
    class Hits:
        def __init__(self, pts):
            self.points = pts

    class Pt:
        def __init__(self, i):
            self.id = f"p{i}"
            self.payload = {"text": f"doc {i}", "metadata": {"src": "test"}}

    class FakeQ:
        async def query_points(self, **kw):
            assert kw["using"] in ("speed_vector", "qwen_vector")
            return Hits([Pt(i) for i in range(kw.get("limit", 5))])

    return FakeQ()


EMBED_OK = {"data": [{"embedding": [0.1] * 8}]}
RERANK_OK = {
    "results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": 0.4}]
}


def make_routes():
    return {
        "8007": EMBED_OK,
        "8004": EMBED_OK,
        "8008": RERANK_OK,
        "8003": RERANK_OK,
    }


@pytest.mark.asyncio
async def test_short_query_routes_to_speed(monkeypatch):
    monkeypatch.setattr(dual, "COLLECTION", "test")
    sess = FakeSession(make_routes())
    r = dual.DualTierRetriever(qdrant=fake_qdrant([]), http=sess)
    out = await r.retrieve("is this token a rug pull?")
    assert out.tier == "speed"
    assert not out.fallback_used
    assert any("8007" in c for c in sess.calls), "speed embed endpoint must be hit"


@pytest.mark.asyncio
async def test_long_query_routes_to_deep(monkeypatch):
    monkeypatch.setattr(dual, "COLLECTION", "test")
    sess = FakeSession(make_routes())
    r = dual.DualTierRetriever(qdrant=fake_qdrant([]), http=sess)
    long_q = (
        "Give me a complete forensic breakdown of this token launch covering the "
        "liquidity concentration profile across every pool, the deployer wallet "
        "funding history including any mixer exposure found along the path, the "
        "mint authority and ownership renunciation status, all lockup contracts "
        "with their expiry schedules, and every whale wallet that accumulated "
        "positions before trading went live on decentralized exchanges."
    )
    out = await r.retrieve(long_q)
    assert out.tier == "deep"
    assert any("8004" in c for c in sess.calls), "deep embed endpoint must be hit"


@pytest.mark.asyncio
async def test_speed_failure_fails_open_to_deep(monkeypatch):
    monkeypatch.setattr(dual, "COLLECTION", "test")
    routes = make_routes()
    sess = FakeSession(routes, fail={"8007"})  # speed embed down
    r = dual.DualTierRetriever(qdrant=fake_qdrant([]), http=sess)
    out = await r.retrieve("rug pull check")
    assert out.tier == "deep"
    assert out.fallback_used


@pytest.mark.asyncio
async def test_both_down_returns_empty_never_raises(monkeypatch):
    monkeypatch.setattr(dual, "COLLECTION", "test")

    class DeadSession(FakeSession):
        def post(self, url, json=None, **kw):
            raise aiohttp_fail()

    r = dual.DualTierRetriever(qdrant=fake_qdrant([]), http=DeadSession({}, set()))
    out = await r.retrieve("anything")
    assert out.tier == "none"
    assert out.contexts == []


@pytest.mark.asyncio
async def test_instruction_library_applied_to_rerank_query(monkeypatch):
    monkeypatch.setattr(dual, "COLLECTION", "test")
    captured = {}

    class CapturingSession(FakeSession):
        def post(self, url, json=None, **kw):
            if "/rerank" in url:
                captured["query"] = json["query"]
            return super().post(url, json=json, **kw)

    sess = CapturingSession(make_routes())
    r = dual.DualTierRetriever(qdrant=fake_qdrant([]), http=sess)
    await r.retrieve("is this token a rug pull?")
    assert "[TASK]" in captured["query"], "vertical instruction must be embedded"
    assert "evidentiary value" in captured["query"]


@pytest.mark.asyncio
async def test_recall_limits_per_tier(monkeypatch):
    monkeypatch.setattr(dual, "COLLECTION", "test")
    seen = {}

    class RecordingQ(fake_qdrant([]).__class__):
        async def query_points(self, **kw):
            seen["limit"] = kw.get("limit")
            seen["using"] = kw["using"]
            return await super().query_points(**kw)

    sess = FakeSession(make_routes())
    r = dual.DualTierRetriever(qdrant=RecordingQ(), http=sess)
    await r.retrieve("quick check")
    assert seen == {"limit": dual.SPEED_RECALL, "using": "speed_vector"}


def test_pick_tier_threshold():
    assert dual.pick_tier("short query here") == "speed"
    long_q = " ".join(["word"] * dual.ROUTING_THRESHOLD_WORDS)
    assert dual.pick_tier(long_q) == "deep"
