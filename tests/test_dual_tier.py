"""Dual-tier retriever tests — the CURRENT contract, after the 6c24960 seam repair.

What changed and why this file was rewritten rather than patched:

* ``dual_tier`` no longer owns an HTTP session or an ``ENDPOINTS`` map. It embeds
  through the canonical in-process seam (``embed_query``) and takes DI seams
  ``embedder=``/``reranker=``. Constructing it with ``http=`` was a ``TypeError``.
* The fail-open behaviour was **deliberately removed**. A dead embedder used to
  return "success with zero contexts", which a grader could not distinguish
  from "searched honestly, matched nothing". It now returns an explicit
  ``tier="unavailable"`` outcome carrying a ``reason``. ``test_*_returns_empty_*``
  is gone; ``test_dead_embedder_is_unavailable_not_empty`` asserts the inverse.
* ``ENDPOINTS`` moved to ``scripts/dual_tier/engine_endpoints.py`` — the gateway
  proxies to engine processes; the retriever owns no URL. The tier-label
  equivalence test now lives here against the engine module.

All Qdrant and embedder interactions are injected; no live services required.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

importlib.import_module("rugbrain.search.rerank")  # ensure instruction lib loads first
dual = importlib.import_module("rugbrain.search.dual_tier")
dim_guard = importlib.import_module("rugbrain.search.dim_guard")
endpoints = importlib.import_module("scripts.dual_tier.engine_endpoints")

LONG_QUERY = (
    "Give me a complete forensic breakdown of this token launch covering the "
    "liquidity concentration profile across every pool, the deployer wallet "
    "funding history including any mixer exposure found along the path, the "
    "mint authority and ownership renunciation status, all lockup contracts "
    "with their expiry schedules, and every whale wallet that accumulated "
    "positions before trading went live on decentralized exchanges."
)


def fake_qdrant(n: int = 3):
    """Qdrant stub returning *n* points per query, recording the vector names."""

    class Q:
        def __init__(self) -> None:
            self.using: list[str | None] = []
            self.limits: list[int] = []

        async def query_points(self, **kw):
            self.using.append(kw.get("using"))
            self.limits.append(kw.get("limit", 0))

            class Pt:
                def __init__(self, i: int) -> None:
                    self.id = f"p{i}"
                    # Real corpora store 'content'; 'text' exists in almost no
                    # collection and is exactly why contexts used to come back
                    # with an empty body.
                    self.payload = {
                        "content": f"content {i}",
                        "text": f"doc {i}",
                        "metadata": {"src": "test"},
                    }

            class Hits:
                def __init__(self, pts: Any) -> None:
                    self.points = pts

            return Hits([Pt(i) for i in range(kw.get("limit", n))])

        async def get_collection(self, name: str) -> Any:
            """The dim guard reads the live collection's named-vector config."""

            class Cfg:
                def __init__(self, dim: int) -> None:
                    self.size = dim

            class Params:
                def __init__(self, dim: int) -> None:
                    # TIER_PLAN maps BOTH tiers to vector_name=None, so the
                    # unnamed (default) vector is the one the guard resolves.
                    self.vectors = {
                        None: Cfg(dim),
                        "speed_vector": Cfg(dim),
                        "qwen_vector": Cfg(dim),
                    }

            class Config:
                def __init__(self, dim: int) -> None:
                    self.params = Params(dim)

            class Info:
                def __init__(self, dim: int) -> None:
                    # The guard reads info.config.params.vectors — the Qdrant
                    # CollectionInfo shape, not the params object itself.
                    self.config = Config(dim)

            return Info(dim_guard.configured_dim())

    return Q()


def embedder_ok(text: str) -> list[float]:
    """Canonical-seam stand-in. Records what it was asked to embed.

    The seam is ``async def _embed`` — it awaits the injected callable — so a
    sync embedder is not a valid injection and raises a ``TypeError`` deep in
    the retriever rather than at construction. Keep this one async.
    """
    embedder_ok.seen.append(text)  # type: ignore[attr-defined]
    return [0.1] * dim_guard.configured_dim()


embedder_ok.seen = []  # type: ignore[attr-defined]


async def async_embedder_ok(text: str) -> list[float]:
    return embedder_ok(text)


async def async_reranker_ok(
    query: str, candidates: list[dict[str, Any]], tier: str = "", **kw: Any
):
    """Rerank stand-in keeping every candidate.

    The seam calls ``reranker(query, candidates, tier)`` — the THIRD positional
    is the tier label, not ``top_n``. A fake with the old rerank signature
    binds the tier string where it expects an int and dies in a slice.
    """
    async_reranker_ok.tiers.append(tier)  # type: ignore[attr-defined]
    return list(candidates)


async_reranker_ok.tiers = []  # type: ignore[attr-defined]


# ── routing ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_short_query_routes_to_speed():
    q = fake_qdrant()
    r = dual.DualTierRetriever(qdrant=q, embedder=async_embedder_ok, reranker=async_reranker_ok)
    out = await r.retrieve("is this token a rug pull?")
    assert out.tier == "speed"
    assert out.ok is True
    assert not out.fallback_used


@pytest.mark.asyncio
async def test_long_query_routes_to_deep():
    q = fake_qdrant()
    r = dual.DualTierRetriever(qdrant=q, embedder=async_embedder_ok, reranker=async_reranker_ok)
    out = await r.retrieve(LONG_QUERY)
    assert out.tier == "deep"
    assert out.ok is True


@pytest.mark.asyncio
async def test_speed_label_shares_the_deep_lane():
    """0.6b retired 2026-09-14: both labels are the canonical 4B engine."""
    assert endpoints.ENDPOINTS["speed_embed"] == endpoints.ENDPOINTS["deep_embed"]
    assert endpoints.ENDPOINTS["speed_rerank"] == endpoints.ENDPOINTS["deep_rerank"]


# ── the no-silent-degradation contract ───────────────────────────────────────


@pytest.mark.asyncio
async def test_dead_embedder_is_unavailable_not_empty():
    """A dead embedder is an explicit outcome, never an empty success.

    This is the exact behaviour the old ``test_both_down_returns_empty_never_raises``
    pinned and that 6c24960 removed. Reinstating the old assertion would
    re-grant a query path that reports "found nothing" when it in fact found
    nothing *because it could not look*.
    """

    async def dead_embedder(text: str) -> list[float]:
        raise ConnectionError("embedder socket closed")

    r = dual.DualTierRetriever(qdrant=fake_qdrant(), embedder=dead_embedder)
    out = await r.retrieve("anything")

    assert out.tier == dual.TIER_UNAVAILABLE
    assert out.ok is False
    assert out.contexts == []
    assert out.reason, "an unavailable result MUST carry a reason"
    assert "embedder_unavailable" in out.reason
    # The decisive property: not indistinguishable from an honest empty result.
    honest_empty = await dual.DualTierRetriever(
        qdrant=fake_qdrant(n=0), embedder=async_embedder_ok, reranker=async_reranker_ok
    ).retrieve("anything")
    assert honest_empty.tier != out.tier
    assert honest_empty.reason == ""


@pytest.mark.asyncio
async def test_ok_tier_with_no_hits_is_a_distinct_outcome():
    """Search ran, matched nothing — the legitimate empty result."""
    r = dual.DualTierRetriever(
        qdrant=fake_qdrant(n=0), embedder=async_embedder_ok, reranker=async_reranker_ok
    )
    out = await r.retrieve("a query with no matches")
    assert out.tier in ("speed", "deep")
    assert out.ok is True
    assert out.reason == "", "an honest empty result carries NO failure reason"


# ── contexts carry real text ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_contexts_read_content_not_text():
    """The corpus stores 'content'. A 'text'-only payload key yields empty bodies."""
    q = fake_qdrant(n=2)
    r = dual.DualTierRetriever(qdrant=q, embedder=async_embedder_ok, reranker=async_reranker_ok)
    out = await r.retrieve("liquidity collapse evidence please")
    assert out.contexts
    for c in out.contexts:
        assert c.get("text"), f"context body empty — payload key regression: {c!r}"


@pytest.mark.asyncio
async def test_instruction_library_applied_to_rerank_query():
    """A vertical-matched query reaches the reranker with its instruction prefix.

    The prefix is injected by ``apply_instruction`` before the rerank call, not
    by the retriever, so the assertion is on what the reranker RECEIVED.
    """
    seen: list[str] = []

    async def recording_reranker(
        query: str, candidates: list[dict[str, Any]], tier: str = "", **kw: Any
    ):
        seen.append(query)
        return list(candidates)

    r = dual.DualTierRetriever(
        qdrant=fake_qdrant(), embedder=async_embedder_ok, reranker=recording_reranker
    )
    await r.retrieve("sql table of scam wallets")
    assert seen, "reranker was never called"
    assert seen[0] != "sql table of scam wallets"
    assert "[TASK]" in seen[0] and "[QUERY]" in seen[0]


@pytest.mark.asyncio
async def test_tier_recall_depths_differ():
    """speed is a wide sweep, deep is a narrow one — both on the default vector."""
    q = fake_qdrant(n=1)
    r = dual.DualTierRetriever(qdrant=q, embedder=async_embedder_ok, reranker=async_reranker_ok)
    await r.retrieve("short one")
    assert q.using[-1] is None
    assert q.limits[-1] == dual.SPEED_RECALL
    q2 = fake_qdrant(n=1)
    r2 = dual.DualTierRetriever(qdrant=q2, embedder=async_embedder_ok, reranker=async_reranker_ok)
    await r2.retrieve(LONG_QUERY)
    assert q2.limits[-1] == dual.DEEP_RECALL
    assert dual.DEEP_RECALL < dual.SPEED_RECALL


# ── the gateway still proxies to real engine processes ───────────────────────


class _FakeEmbedResp:
    def __init__(self) -> None:
        self.status = 200

    async def __aenter__(self) -> _FakeEmbedResp:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def json(self) -> dict[str, Any]:
        return {"data": [{"embedding": [0.1] * 8}]}


class _FakeGatewaySession:
    last_url = ""

    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeGatewaySession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def post(self, url: str, json=None, **kw: Any) -> _FakeEmbedResp:
        _FakeGatewaySession.last_url = url
        return _FakeEmbedResp()


def _load_gateway():
    """Importing the gateway must not raise — it was broken by a bad import."""
    return importlib.import_module("scripts.dual_tier.model_gateway")


def test_gateway_imports_cleanly():
    """The 6c24960 seam repair deleted ENDPOINTS but left the gateway importing it."""
    gw = _load_gateway()
    assert gw.ENDPOINTS is endpoints.ENDPOINTS
    assert isinstance(gw._retriever, dual.DualTierRetriever)


@pytest.mark.asyncio
async def test_gateway_embed_defaults_to_deep(monkeypatch):
    """tier=None routes to the CANONICAL deep lane (2026-08-29 doctrine)."""
    gw = _load_gateway()
    monkeypatch.setattr(gw.aiohttp, "ClientSession", _FakeGatewaySession)
    out = await gw.embed(gw.EmbedReq(text="rug pull check"))
    assert out["tier"] == "deep", "None tier must default to the canonical deep lane"
    assert "8004" in _FakeGatewaySession.last_url
