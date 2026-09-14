"""RugBrain rerank lane contracts (hosted live lane -> local 4B).

Offline: _post_rerank stubbed. Contracts:

* live tier caps candidates and serves from the local 4B; hosted tried first
* hosted unavailable -> local 4B serves (capped); 0.6B tier retired 2026-09-14
* all lanes down -> ([], "none") fail-open
* strain breaker skips a failing endpoint entirely after threshold
* deep tier (default) unchanged single-shot behavior
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
async def test_live_tier_serves_from_the_local_4b(monkeypatch):
    calls: list[tuple[str, int]] = []

    async def fake_post(url, query, docs, timeout, label):  # noqa: ASYNC109 — mirrors real signature
        calls.append((url, len(docs)))
        return [{"index": i, "relevance_score": 0.9} for i in range(len(docs))]

    monkeypatch.setattr(rr, "_post_rerank", fake_post)
    docs = [{"content": f"d{i}"} for i in range(100)]
    scores, method = await rr._score_candidates("rug", docs, tier="live")
    assert method == "qwen3-reranker-4b"
    assert calls[0][0] == rr.RERANKER_URL
    assert calls[0][1] == min(rr.LIVE_CANDIDATE_CAP, rr.DEEP_FALLBACK_CAP)
    assert len(scores) == len(docs)  # full score vector preserved


@pytest.mark.asyncio
async def test_live_ladder_hosted_then_local(monkeypatch):
    """Hosted rung tried first (the 0.6B replacement); local 4B serves on failure."""
    monkeypatch.setattr(rr, "hosted_enabled", lambda: True)

    async def fake_hosted(query, docs, top_n=0, **kw):
        return None  # hosted unavailable

    calls: list[tuple[str, int]] = []

    async def fake_post(url, query, docs, timeout, label):  # noqa: ASYNC109 — mirrors real signature
        calls.append((url, len(docs)))
        return [{"index": i, "relevance_score": 0.8} for i in range(len(docs))]

    monkeypatch.setattr(rr, "hosted_rerank", fake_hosted)
    monkeypatch.setattr(rr, "_post_rerank", fake_post)
    docs = [{"content": f"doc{i}"} for i in range(60)]
    scores, method = await rr._score_candidates("q", docs, tier="live")
    assert method == "qwen3-reranker-4b"
    assert calls[0][0] == rr.RERANKER_URL
    assert calls[0][1] == min(rr.LIVE_CANDIDATE_CAP, rr.DEEP_FALLBACK_CAP)
    assert any(s > 0 for s in scores)


@pytest.mark.asyncio
async def test_both_tiers_down_fail_open(monkeypatch):
    async def none_post(url, query, docs, timeout, label):  # noqa: ASYNC109 — mirrors real signature
        return None

    monkeypatch.setattr(rr, "_post_rerank", none_post)
    scores, method = await rr._score_candidates("q", [{"content": "a"}], tier="live")
    assert scores == [] and method == "none"


@pytest.mark.asyncio
async def test_deep_default_single_shot(monkeypatch):
    calls: list[str] = []

    async def fake_post(url, query, docs, timeout, label):  # noqa: ASYNC109 — mirrors real signature
        calls.append(url)
        return [{"index": 0, "relevance_score": 0.7}]

    monkeypatch.setattr(rr, "_post_rerank", fake_post)
    out = await rr._score_candidates("q", [{"content": "a"}], tier="deep")
    assert out[1] == "qwen3-reranker-4b"
    assert calls == [rr.RERANKER_URL]


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
