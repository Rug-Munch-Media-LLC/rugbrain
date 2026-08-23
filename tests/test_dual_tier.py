"""Dual-tier retriever tests — routing, fail-open, instruction propagation.

All network and Qdrant interactions are mocked; no live services required.
"""

from __future__ import annotations

import importlib
from typing import Any, ClassVar

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


# --- Gateway /embed auto-routing regression (2026-08-23) ---------------------
# Bug: EmbedReq.tier defaulted to "speed" AND the text was never passed to
# pick_tier, so /embed ALWAYS hit the speed engine regardless of length.
# Contract: tier=None -> word-count auto-route; explicit tier still wins.


class _FakeEmbedResp:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return EMBED_OK


class _FakeGatewaySession:
    """Stands in for aiohttp.ClientSession inside model_gateway endpoints."""

    last_url: str = ""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, json=None, **kw):
        _FakeGatewaySession.last_url = url
        return _FakeEmbedResp()


def _load_gateway():
    gw = importlib.import_module("scripts.dual_tier.model_gateway")
    return gw


@pytest.mark.asyncio
async def test_gateway_embed_autoroutes_long_text(monkeypatch):
    gw = _load_gateway()
    monkeypatch.setattr(gw.aiohttp, "ClientSession", _FakeGatewaySession)
    out = await gw.embed(gw.EmbedReq(text=" ".join(["word"] * 60)))
    assert out["tier"] == "deep", "long text must auto-route to deep embed"
    assert "8004" in _FakeGatewaySession.last_url


@pytest.mark.asyncio
async def test_gateway_embed_autoroutes_short_text(monkeypatch):
    gw = _load_gateway()
    monkeypatch.setattr(gw.aiohttp, "ClientSession", _FakeGatewaySession)
    out = await gw.embed(gw.EmbedReq(text="rug pull check"))
    assert out["tier"] == "speed", "short text must auto-route to speed embed"
    assert "8007" in _FakeGatewaySession.last_url


@pytest.mark.asyncio
async def test_gateway_embed_explicit_tier_wins(monkeypatch):
    gw = _load_gateway()
    monkeypatch.setattr(gw.aiohttp, "ClientSession", _FakeGatewaySession)
    out = await gw.embed(gw.EmbedReq(text="short", tier="deep"))
    assert out["tier"] == "deep", "explicit tier must override auto-routing"
    assert "8004" in _FakeGatewaySession.last_url


# --- Cloud fallback + strain breaker (2026-08-23) ---------------------------
# Doctrine: local canon is 4B/0.6B quantized. Cloud engages only on failure or
# open breaker; responses carry served_by=cloud + degraded=True. Rerank stays
# LOCAL (4B -> 0.6B), never cloud.


class _RoutingResp:
    status = 200

    def __init__(self, payload):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


def _make_routing_session(fail_markers=("8004",)):
    class Sess:
        last_urls: ClassVar[list[str]] = []

        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None, **kw):
            Sess.last_urls.append(url)
            if any(m in url for m in fail_markers):
                import aiohttp

                raise aiohttp.ClientError("simulated local outage")
            if "/embeddings" in url:
                return _RoutingResp(EMBED_OK)
            return _RoutingResp(RERANK_OK)

    return Sess


@pytest.mark.asyncio
async def test_deep_embed_cloud_fallback_on_outage(monkeypatch):
    gw = _load_gateway()
    monkeypatch.setattr(gw.aiohttp, "ClientSession", _make_routing_session(("8004",)))
    monkeypatch.setattr(gw, "_CLOUD_KEYS", {"speed": "", "deep": "test-key"})
    out = await gw.embed(gw.EmbedReq(text="forensic query", tier="deep"))
    assert out["served_by"] == "cloud"
    assert out["degraded"] is True
    assert out["tier"] == "deep"
    assert "qwen3-embedding-8b" in out["cloud_model"]


@pytest.mark.asyncio
async def test_speed_embed_cloud_fallback_uses_main_free_model(monkeypatch):
    gw = _load_gateway()
    sess = _make_routing_session(("8007",))
    monkeypatch.setattr(gw.aiohttp, "ClientSession", sess)
    monkeypatch.setattr(gw, "_CLOUD_KEYS", {"speed": "main-key", "deep": ""})
    out = await gw.embed(gw.EmbedReq(text="quick check", tier="speed"))
    assert out["served_by"] == "cloud" and out["degraded"] is True
    assert "lfm-2.5-embedding-350m" in out["cloud_model"]
    assert any("openrouter.ai" in u for u in sess.last_urls)


@pytest.mark.asyncio
async def test_no_cloud_key_fails_loud(monkeypatch):
    gw = _load_gateway()
    monkeypatch.setattr(gw.aiohttp, "ClientSession", _make_routing_session(("8004",)))
    monkeypatch.setattr(gw, "_CLOUD_KEYS", {"speed": "", "deep": ""})
    import pytest as _pytest
    from fastapi import HTTPException

    with _pytest.raises(HTTPException):
        await gw.embed(gw.EmbedReq(text="x", tier="deep"))


@pytest.mark.asyncio
async def test_rerank_stays_local_and_falls_to_lighter_tier(monkeypatch):
    gw = _load_gateway()
    sess = _make_routing_session(("8003",))  # deep reranker down
    monkeypatch.setattr(gw.aiohttp, "ClientSession", sess)
    out = await gw.rerank(gw.RerankReq(query="rug pull signs", documents=["a", "b"], tier="deep"))
    assert out["tier"] == "speed" and out.get("degraded") is True
    assert not any("openrouter.ai" in u for u in sess.last_urls), "rerank must never go cloud"


def test_breaker_trips_after_consecutive_failures():
    gw = _load_gateway()
    gw._cloud_fails["deep"] = 0
    gw._breaker_open_until["deep"] = 0.0
    for _ in range(gw._BREAKER_TRIP):
        gw._note_failure("deep")
    assert gw._breaker_open("deep") is True
    gw._breaker_open_until["deep"] = 0.0  # cleanup
