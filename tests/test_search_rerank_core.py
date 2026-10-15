"""Search rerank core contracts (was 55% — lines 253-298 dark).

Offline: the hosted chain is stubbed. Contracts for _score_candidates:

* disabled reranker → ([], "none") without HTTP
* empty candidates → ([], "none")
* valid response: scores mapped back to ORIGINAL indices (empty docs
  skipped in payload but keep 0.0 slots)
* hosted chain unavailable → ([], "none") fail-open

The local llama.cpp :8003 lane (4B/0.6B ladders) was retired 2026-10-11 —
both tiers now resolve through the hosted multi-provider chain
(``rerank_lanes``: deepinfra → siliconflow → openrouter → openrouter2, all
Qwen3-Reranker-8B). The method label is provider-prefixed
(``deepinfra:qwen3-reranker-8b``).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from rugbrain.search import rerank as rr


@pytest.fixture(autouse=True)
def _healthy(monkeypatch):
    monkeypatch.setattr(rr, "RERANKER_ENABLED", True)
    # Hermetic: the hosted chain is stubbed per-test via ``hosted_rerank``;
    # clear global strain state polluted by sibling tests (module-level
    # defaultdict).
    rr._strain_failures.clear()
    rr._health["ok"] = True
    rr._health["last_check"] = time.time()
    yield
    rr._health["ok"] = False
    rr._health["last_check"] = 0.0
    rr._strain_failures.clear()


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


class FakeClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, json=None):
        if self._exc:
            raise self._exc
        return self._resp


@pytest.mark.asyncio
async def test_disabled_short_circuits(monkeypatch):
    monkeypatch.setattr(rr, "RERANKER_ENABLED", False)
    called = {"n": 0}

    class Spy(FakeClient):
        async def post(self, url, json=None):
            called["n"] += 1
            return _resp({"results": []})

    monkeypatch.setattr(rr.httpx, "AsyncClient", lambda **k: Spy())
    scores, method = await rr._score_candidates("q", [{"content": "doc"}])
    assert scores == [] and method == "none"
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_empty_candidates():
    scores, method = await rr._score_candidates("q", [])
    assert scores == [] and method == "none"


@pytest.mark.asyncio
async def test_scores_mapped_back_to_original_indices(monkeypatch):
    # doc 1 is empty after chunking → skipped in payload but keeps slot
    candidates = [
        {"content": "real doc zero"},
        {"content": ""},
        {"content": "real doc two"},
    ]

    async def fake_hosted(query, docs, top_n=0, **kw):
        # Only the two VALID docs reach the hosted chain; the empty one is
        # filtered by valid_docs before the call.
        assert docs == ["real doc zero", "real doc two"]
        return [
            {"index": 0, "relevance_score": 0.9},  # first VALID doc
            {"index": 1, "relevance_score": 0.4},  # second VALID doc
        ]

    monkeypatch.setattr(rr, "hosted_enabled", lambda: True)
    monkeypatch.setattr(rr, "hosted_rerank", fake_hosted)
    scores, method = await rr._score_candidates("rug", candidates)
    assert method == "deepinfra:qwen3-reranker-8b"
    assert len(scores) == 3
    assert scores[0] == pytest.approx(0.9)  # original index 0
    assert scores[1] == 0.0  # empty doc keeps neutral slot
    assert scores[2] == pytest.approx(0.4)  # original index 2


@pytest.mark.asyncio
async def test_hosted_chain_down_fails_open(monkeypatch):
    """Every hosted rung down → ([], "none"); search never breaks."""
    monkeypatch.setattr(rr, "hosted_enabled", lambda: True)

    async def down(query, docs, top_n=0, **kw):
        return None

    monkeypatch.setattr(rr, "hosted_rerank", down)
    scores, method = await rr._score_candidates("q", [{"content": "d"}])
    assert scores == [] and method == "none"


@pytest.mark.asyncio
async def test_hosted_disabled_fails_open(monkeypatch):
    """Hosted chain disabled → ([], "none") without any HTTP."""
    monkeypatch.setattr(rr, "hosted_enabled", lambda: False)
    scores, method = await rr._score_candidates("q", [{"content": "d"}])
    assert scores == [] and method == "none"


@pytest.mark.asyncio
async def test_recent_failure_skips_probe(monkeypatch):
    rr._health["ok"] = False
    rr._health["last_check"] = time.time()  # just failed
    called = {"n": 0}

    async def spy_hosted(query, docs, top_n=0, **kw):
        called["n"] += 1
        return [{"index": 0, "relevance_score": 0.5}]

    monkeypatch.setattr(rr, "hosted_enabled", lambda: True)
    monkeypatch.setattr(rr, "hosted_rerank", spy_hosted)
    scores, method = await rr._score_candidates("q", [{"content": "d"}])
    # The hosted chain is the only lane; a stale health flag does not gate it.
    assert method == "deepinfra:qwen3-reranker-8b"
    assert called["n"] == 1
