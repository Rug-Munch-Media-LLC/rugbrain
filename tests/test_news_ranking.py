"""Tests for news story ranking (rugbrain.news_ranking)."""

from __future__ import annotations

import importlib as _il
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

news_ranking = _il.import_module("rugbrain.news_ranking")
nk = news_ranking

mod = news_ranking
from rugbrain.news_clustering import Story, StorySource


def _story(**overrides: Any) -> Story:
    now = datetime.now(UTC)
    sources = [
        StorySource(
            article_id="s1",
            title="Ethereum ETF approved by SEC",
            url="https://coindesk.com/1",
            source="coindesk.com",
            domain="coindesk.com",
            credibility=95,
            published_at=(now - timedelta(hours=2)).isoformat(),
            summary="The SEC approved spot ether ETFs in a landmark decision.",
        ),
        StorySource(
            article_id="s2",
            title="Ethereum ETF gets green light",
            url="https://theblock.co/2",
            source="theblock.co",
            domain="theblock.co",
            credibility=94,
            published_at=(now - timedelta(hours=3)).isoformat(),
            summary="Regulators approved ether ETFs after months of review.",
        ),
        StorySource(
            article_id="s3",
            title="SEC approves Ethereum ETFs",
            url="https://random-blog.example/3",
            source="random-blog.example",
            domain="random-blog.example",
            credibility=45,
            published_at=(now - timedelta(hours=4)).isoformat(),
            summary="Ethereum ETFs approved, price pumped.",
        ),
    ]
    return Story(
        id="story-1",
        title="Ethereum ETF approved by SEC",
        summary="The SEC approved spot ether ETFs in a landmark decision.",
        sources=sources,
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
    ).model_copy(update=overrides)


# ── Composite scoring ─────────────────────────────────────────────────────


def test_composite_score_weights_sum_to_one() -> None:
    assert sum(nk.RANK_WEIGHTS.values()) == pytest.approx(1.0)


def test_score_story_axes_source_credibility_is_mean() -> None:
    story = _story()
    axes = nk.score_story_axes(story)
    expected = (95 + 94 + 45) / 3
    assert axes["source_credibility"] == pytest.approx(expected, abs=0.2)


def test_score_story_axes_timeliness_uses_newest() -> None:
    story = _story()
    axes = nk.score_story_axes(story)
    # Newest source is 2h old -> high timeliness.
    assert axes["timeliness"] >= 70


def test_composite_score_in_range() -> None:
    story = _story()
    score = nk.composite_story_score(story)
    assert 0 <= score <= 100


def test_composite_score_corroboration_boosted_by_sources() -> None:
    single = _story(sources=_story().sources[:1])
    multi = _story(sources=_story().sources[:3])
    single_axes = nk.score_story_axes(single)
    multi_axes = nk.score_story_axes(multi)
    assert multi_axes["story_corroboration"] > single_axes["story_corroboration"]


def test_story_velocity_sources_per_hour() -> None:
    story = _story()
    now = datetime.now(UTC)
    vel = nk.story_velocity(story, now=now)
    assert vel > 0
    # 3 sources over ~4 hours -> velocity in the 0.5-10 range.
    assert 0.1 <= vel <= 30


# ── Reranking ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_story_sources_qwen3(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Qwen3-Reranker-8B /rerank reorders sources by relevance score."""

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            return {
                "results": [
                    {"index": 2, "relevance_score": 0.99},
                    {"index": 0, "relevance_score": 0.8},
                    {"index": 1, "relevance_score": 0.6},
                ]
            }

    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, timeout: float = 30.0) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> Any:
            return False

        async def post(self, url: Any, json: Any) -> Any:
            captured["url"] = url
            captured["json"] = json
            return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    story = _story()
    ordered, method = await nk.rerank_story_sources(story)
    assert method == "qwen3-reranker-8b"
    assert [s.article_id for s in ordered] == ["s3", "s1", "s2"]
    assert captured["json"]["query"] == story.title
    assert "documents" in captured["json"]


@pytest.mark.asyncio
async def test_rerank_story_sources_fallback(monkeypatch: pytest.MonkeyPatch) -> Any:
    """When the embedder fails, sources sort by credibility (best-effort)."""

    class Boom:
        def __init__(self, timeout: float = 30.0) -> None:
            pass

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> Any:
            return False

        async def post(self, url: Any, json: Any) -> None:
            raise RuntimeError("embedder down")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", Boom)
    story = _story()
    ordered, method = await nk.rerank_story_sources(story)
    assert method == "credibility-fallback"
    assert [s.article_id for s in ordered] == ["s1", "s2", "s3"]


@pytest.mark.asyncio
async def test_rank_story_sets_primary_and_composite() -> None:
    story = _story()
    ranked = await nk.rank_story(story, rerank=False)
    assert ranked.primary_source_id == "s1"  # highest credibility
    assert ranked.composite_score > 0
    assert ranked.velocity > 0
    assert "source_credibility" in ranked.axes


@pytest.mark.asyncio
async def test_reranker_improves_primary_selection(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The reranker promotes a highly-relevant source over a merely
    high-credibility one — the core ranking-quality improvement.

    Without reranking, the primary is picked by credibility alone (s1).
    With the cross-encoder, a low-credibility article whose summary is far
    more relevant to the story title wins the primary slot.
    """
    story = _story()
    # Rerank says the last (low-credibility) source is most relevant.
    relevances = {0: 0.5, 1: 0.6, 2: 0.99}
    captured: dict[str, Any] = {}

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            return {
                "results": [
                    {"index": i, "relevance_score": score} for i, score in relevances.items()
                ]
            }

    class FakeClient:
        def __init__(self, timeout: float = 30.0) -> None:
            pass

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> Any:
            return False

        async def post(self, url: Any, json: Any) -> Any:
            captured["json"] = json
            return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    ranked = await nk.rank_story(story, rerank=True)
    # s3 (random-blog, credibility 45) is top-ranked by the reranker.
    assert ranked.primary_source_id == "s3"
    # The top source carries its cross-encoder score.
    assert ranked.sources[0].rerank_score == pytest.approx(0.99)
    # Rerank relevance axis reflects the cross-encoder scores.
    assert ranked.axes["rerank_relevance"] > 50


# ── Qdrant persistence ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_story_fails_open(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Qdrant unreachable -> store_story returns False (never raises)."""
    import httpx

    class Boom:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> Any:
            return False

        async def get(self, url: Any, **kwargs: Any) -> None:
            raise RuntimeError("qdrant down")

        async def put(self, url: Any, **kwargs: Any) -> None:
            raise RuntimeError("qdrant down")

    monkeypatch.setattr(httpx, "AsyncClient", Boom)
    ok = await nk.store_story(_story())
    assert ok is False


@pytest.mark.asyncio
async def test_load_story_parses_payload(monkeypatch: pytest.MonkeyPatch) -> Any:
    """load_story reconstructs a Story from a stored payload."""
    import httpx

    story = _story()
    payload = nk._story_payload(story)

    class FakeResp:
        def __init__(self, status_code: int = 200) -> None:
            self.status_code = status_code

        def json(self) -> Any:
            return {"result": {"payload": payload}}

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> Any:
            return False

        async def get(self, url: Any, params: Any = None) -> Any:
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    loaded = await nk.load_story(story.id)
    assert loaded is not None
    assert loaded.id == story.id
    assert len(loaded.sources) == 3


@pytest.mark.asyncio
async def test_load_story_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> Any:
    import httpx

    class FakeResp:
        def __init__(self, status_code: int = 404) -> None:
            self.status_code = status_code

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> Any:
            return False

        async def get(self, url: Any, params: Any = None) -> Any:
            return FakeResp(404)

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    assert await nk.load_story("missing") is None


@pytest.mark.asyncio
async def test_ranked_feed_fails_open(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Qdrant unreachable -> empty feed (fail-open, no exception)."""
    import httpx

    class Boom:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *args: Any) -> Any:
            return False

        async def post(self, url: Any, **kwargs: Any) -> None:
            raise RuntimeError("qdrant down")

    monkeypatch.setattr(httpx, "AsyncClient", Boom)
    feed = await nk.ranked_feed(topic="security", min_quality=70, limit=20)
    assert feed == []


def test_story_payload_has_facets() -> None:
    payload = nk._story_payload(_story())
    assert payload["type"] == "news_story"
    assert payload["story_id"] == "story-1"
    assert payload["source_count"] == 3
    assert isinstance(payload["story"], dict)
