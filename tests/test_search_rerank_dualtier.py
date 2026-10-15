"""RugBrain rerank lane contracts (hosted-only chain, 8B).

The local llama.cpp :8003 lane (the 4B/0.6B ladders) was retired 2026-10-11
(operator directive: 8B ONLY). Both tiers now resolve through the hosted
multi-provider chain (``rerank_lanes``: deepinfra → siliconflow → openrouter
→ openrouter2, all Qwen3-Reranker-8B). The method label is provider-prefixed
(``deepinfra:qwen3-reranker-8b``).

Offline: ``hosted_rerank`` stubbed. Contracts:

* live tier caps candidates and serves from the hosted chain
* hosted chain unavailable -> ([], "none") fail-open
* deep tier (default) unchanged single-shot behavior
* strain breaker skips a failing endpoint entirely after threshold
"""

from __future__ import annotations

import time

import pytest

from rugbrain.search import rerank as rr


@pytest.fixture(autouse=True)
def _clean_strain_and_health():
    """Strain ledger cleared + health marked fresh-OK so deep-path probes
    are deterministic regardless of pollution from other test modules."""
    rr._clear_strain()
    rr._health["ok"] = True
    rr._health["last_check"] = time.time()
    yield
    rr._clear_strain()


@pytest.mark.asyncio
async def test_live_tier_serves_from_the_hosted_chain(monkeypatch):
    calls: list[int] = []

    async def fake_hosted(query, docs, top_n=0, **kw):
        calls.append(len(docs))
        return [{"index": i, "relevance_score": 0.9} for i in range(len(docs))]

    monkeypatch.setattr(rr, "hosted_enabled", lambda: True)
    monkeypatch.setattr(rr, "hosted_rerank", fake_hosted)
    docs = [{"content": f"d{i}"} for i in range(100)]
    scores, method = await rr._score_candidates("rug", docs, tier="live")
    assert method == "deepinfra:qwen3-reranker-8b"
    assert calls[0] == min(rr.HOSTED_CANDIDATE_CAP, len(docs))
    assert len(scores) == len(docs)  # full score vector preserved


@pytest.mark.asyncio
async def test_live_ladder_hosted_chain(monkeypatch):
    """The hosted chain is the only rung; it serves the live tier."""
    monkeypatch.setattr(rr, "hosted_enabled", lambda: True)

    calls: list[int] = []

    async def fake_hosted(query, docs, top_n=0, **kw):
        calls.append(len(docs))
        return [{"index": i, "relevance_score": 0.8} for i in range(len(docs))]

    monkeypatch.setattr(rr, "hosted_rerank", fake_hosted)
    docs = [{"content": f"doc{i}"} for i in range(60)]
    scores, method = await rr._score_candidates("q", docs, tier="live")
    assert method == "deepinfra:qwen3-reranker-8b"
    assert calls[0] == min(rr.HOSTED_CANDIDATE_CAP, len(docs))
    assert any(s > 0 for s in scores)


@pytest.mark.asyncio
async def test_hosted_chain_down_fail_open(monkeypatch):
    async def none_hosted(query, docs, top_n=0, **kw):
        return None

    monkeypatch.setattr(rr, "hosted_enabled", lambda: True)
    monkeypatch.setattr(rr, "hosted_rerank", none_hosted)
    scores, method = await rr._score_candidates("q", [{"content": "a"}], tier="live")
    assert scores == [] and method == "none"


@pytest.mark.asyncio
async def test_deep_default_single_shot(monkeypatch):
    calls: list[int] = []

    async def fake_hosted(query, docs, top_n=0, **kw):
        calls.append(len(docs))
        return [{"index": 0, "relevance_score": 0.7}]

    monkeypatch.setattr(rr, "hosted_enabled", lambda: True)
    monkeypatch.setattr(rr, "hosted_rerank", fake_hosted)
    out = await rr._score_candidates("q", [{"content": "a"}], tier="deep")
    assert out[1] == "deepinfra:qwen3-reranker-8b"
    assert calls == [1]


@pytest.mark.asyncio
async def test_rerank_chunks_tier_passthrough(monkeypatch):
    seen: dict = {}

    async def fake_score(query, candidates, tier="deep"):
        seen["tier"] = tier
        return [0.9] * len(candidates), "test"

    monkeypatch.setattr(rr, "_score_candidates", fake_score)
    chunks = [{"id": "1", "content": "c"}]
    await rr.rerank_chunks("q", chunks, top_n=1, keep=1, tier="live")
    assert seen["tier"] == "live"


def test_strain_breaker_skips_failing_endpoint(monkeypatch):
    """The strain ledger marks a failing lane so _post_rerank skips it."""
    for _ in range(rr._STRAIN_THRESHOLD):
        rr._record_strain(rr.RERANKER_URL)
    assert rr._is_strained(rr.RERANKER_URL) is True
