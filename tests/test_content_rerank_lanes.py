"""Content-pipeline rerank: local 8B lane, hosted rung, fail-open.

Regression target (2026-10-24): ``search.rerank_pipeline.tag_unreranked``
returns stories WITHOUT ``rerank_score`` when no reranker answered, and the
content pipeline's score filter then dropped every story — a rerank outage
silently published nothing. Fail-open must return the retrieval order, tagged
with the reason.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from rugbrain import content_rerank


def _story(idx: int, score: float, content: str = "") -> dict[str, Any]:
    return {
        "id": f"p{idx}",
        "title": f"story {idx}",
        "content": content or f"body {idx}",
        "severity": "high",
        "source": "news_v2",
        "url": f"https://example.test/{idx}",
        "importance": 0.5,
        "score": score,
    }


def _local_answer(scores: dict[int, float], seen: dict[str, Any] | None = None) -> Any:
    """Fake httpx client that answers like llama.cpp ``/v1/rerank``."""

    class _Client:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def post(self, url: str, *, headers: Any = None, json: Any = None) -> Any:
            if seen is not None:
                seen["url"] = url
                seen["headers"] = headers or {}
                seen["json"] = json or {}
            results = [
                {"index": i, "relevance_score": s}
                for i, s in scores.items()
                if i < len(json["documents"])
            ]
            return httpx.Response(200, json={"results": results}, request=httpx.Request("POST", url))

    return _Client


def _local_down(*a: Any, **kw: Any) -> Any:
    class _Client:
        def __init__(self, *c: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def post(self, *a: Any, **kw: Any) -> Any:
            raise httpx.ConnectError("lane down")

    return _Client


@pytest.mark.asyncio
async def test_local_8b_lane_orders_and_authenticates(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setenv("QWEN_EMBED_API_KEY", "test-key")
    monkeypatch.setattr(
        content_rerank.httpx, "AsyncClient", _local_answer({0: 0.05, 1: 0.91}, seen)
    )
    stories = [_story(0, 0.9, "off-topic market recap"), _story(1, 0.2, "exploit drained 4M")]

    out = await content_rerank.rerank_stories(stories, keep=2)

    assert [s["id"] for s in out] == ["p1"], "below-floor story must be dropped"
    assert out[0]["rerank_score"] == pytest.approx(0.91)
    assert out[0]["rerank_method"] == "local-8003:qwen3-reranker-8b"
    assert seen["url"] == "http://127.0.0.1:8003/rerank"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["json"]["model"] == "qwen3-reranker-8b"
    assert len(seen["json"]["documents"]) == 2


@pytest.mark.asyncio
async def test_fail_open_returns_retrieval_order_with_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No reranker anywhere → never an empty list, always a reason."""
    monkeypatch.setattr(content_rerank.httpx, "AsyncClient", _local_down())
    monkeypatch.setattr(content_rerank, "_hosted_scores", _hosted_none)

    out = await content_rerank.rerank_stories(
        [_story(0, 0.2), _story(1, 0.8), _story(2, 0.5)], keep=2
    )

    assert [s["id"] for s in out] == ["p1", "p2"], "retrieval order, not empty"
    assert all(s["rerank_method"] == "none" for s in out)
    assert all("rerank_unavailable" in s["rerank_reason"] for s in out)


async def _hosted_none(*a: Any, **kw: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_hosted_rung_used_when_local_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content_rerank.httpx, "AsyncClient", _local_down())
    monkeypatch.setattr(content_rerank, "_hosted_scores", _hosted_answer)

    out = await content_rerank.rerank_stories([_story(0, 0.2), _story(1, 0.8)], keep=2)

    assert [s["id"] for s in out] == ["p1", "p0"]
    assert out[0]["rerank_method"] == "hosted:deepinfra:qwen3-reranker-8b"


async def _hosted_answer(
    intent: str, candidates: list[dict[str, Any]], pool: int
) -> tuple[list[tuple[dict[str, Any], float]], str]:
    pairs = [(c, 0.9 if c["id"] == "p1" else 0.4) for c in candidates]
    return pairs, "hosted:deepinfra:qwen3-reranker-8b"


@pytest.mark.asyncio
async def test_all_below_floor_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(content_rerank.httpx, "AsyncClient", _local_answer({0: 0.01, 1: 0.02}))

    out = await content_rerank.rerank_stories([_story(0, 0.9), _story(1, 0.8)])

    assert out == [], "an answered-but-irrelevant pool is honestly empty"


@pytest.mark.asyncio
async def test_empty_input_short_circuits() -> None:
    assert await content_rerank.rerank_stories([]) == []
