"""The rerank wire contract: one join rule, and the lane that answered is named.

2026-10-23 — three defects, one contract:
  * ``rerank_endpoint`` is idempotent on ``/rerank`` (callers used to double it
    when RERANKER_URL already ended in ``/rerank``, the shape every hosted
    provider publishes);
  * ``hosted_rerank`` tags its result with the lane that answered;
  * the search provenance label names that lane instead of hardcoding
    ``deepinfra`` (deepinfra is now the LAST rung and usually contributes
    nothing).
"""

from __future__ import annotations

import pytest

from rugbrain import rerank_compat as rc
from rugbrain import rerank_lanes as rl
from rugbrain.rerank_endpoint import rerank_endpoint
from rugbrain.rerank_lanes import RerankScores
from rugbrain.search import rerank as rr


@pytest.mark.parametrize(
    ("base", "want"),
    [
        ("http://127.0.0.1:8003", "http://127.0.0.1:8003/rerank"),
        ("http://127.0.0.1:8003/", "http://127.0.0.1:8003/rerank"),
        ("https://api.siliconflow.com/v1/rerank", "https://api.siliconflow.com/v1/rerank"),
        ("", ""),
    ],
)
def test_rerank_endpoint_is_idempotent(base: str, want: str) -> None:
    assert rerank_endpoint(base) == want


@pytest.mark.asyncio
async def test_provenance_names_the_lane_that_answered(monkeypatch) -> None:
    async def fake_hosted(query, docs, top_n=0, **kw):
        tagged = RerankScores([{"index": 0, "relevance_score": 0.9}])
        tagged.lane = "siliconflow"
        return tagged

    monkeypatch.setattr(rr, "hosted_enabled", lambda: True)
    monkeypatch.setattr(rr, "hosted_rerank", fake_hosted)
    scores, method = await rr._score_candidates("q", [{"content": "a"}])
    assert scores == [0.9]
    assert method == "siliconflow:qwen3-reranker-8b"


@pytest.mark.asyncio
async def test_hosted_rerank_tags_the_winning_lane(monkeypatch) -> None:
    async def call(provider, query, documents, timeout):
        return [{"index": 0, "relevance_score": 0.5}] if provider == "openrouter" else None

    monkeypatch.setattr(rl, "_call", call)
    monkeypatch.setattr(rl, "_key", lambda provider: "k")
    monkeypatch.setattr(rl, "_order", lambda: ("siliconflow", "openrouter"))
    out = await rl.hosted_rerank("q", ["d"])
    assert out is not None
    assert out.lane == "openrouter"


@pytest.mark.asyncio
async def test_rerank_tiered_posts_to_one_rerank_path(monkeypatch) -> None:
    seen: list[str] = []

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"results": [{"index": 0, "relevance_score": 1.0}]}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            seen.append(url)
            return _Resp()

    monkeypatch.setattr(rc.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(rc, "RERANK_FAST_URL", "https://api.siliconflow.com/v1/rerank")
    out = await rc.rerank_tiered("q", ["d"], tier="fast")
    assert seen == ["https://api.siliconflow.com/v1/rerank"]
    assert out == [{"index": 0, "relevance_score": 1.0}]


@pytest.mark.asyncio
async def test_rerank_tiered_noops_without_url(monkeypatch) -> None:
    monkeypatch.setattr(rc, "RERANKER_URL", "")
    monkeypatch.setattr(rc, "RERANK_FAST_URL", "")
    assert await rc.rerank_tiered("q", ["d"]) == []
