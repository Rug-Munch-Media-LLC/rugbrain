"""
News story ranking — rerank sources within a story and rank the feed.

Part 2 of the Ground News / CryptoPanic-beating news aggregation system.

For every story cluster, articles are reranked against the story title
using the self-hosted Qwen3-Reranker-8B (llama.cpp port 8003); the best
article becomes the story's primary source. Stories themselves are scored
on five weighted axes and the resulting scores are persisted to the Qdrant
``news_v2`` collection:

    source_credibility  30%  — mean source reputation of the story's sources
    factual_density     20%  — verifiable-claims density across summaries
    timeliness          15%  — how fresh the latest source is
    story_corroboration 20%  — how many independent sources cover it
    rerank_relevance    15%  — mean cross-encoder relevance of sources

Qdrant persistence
------------------
Stories are stored as points in the ``news_v2`` collection (named dense
vector ``Qwen3-Embedding-8B`` 2560-dim, consistent with the existing collection). Each
point payload carries the full story JSON, plus ``type="news_story"`` and
``topics``/``composite_score``/``source_count``/``updated_at`` fields so
the feed API can filter and sort without loading every payload.

Usage::

    from rugbrain.news_ranking import rank_and_store_story

    story = await rank_and_store_story(story)          # rerank + score + store
    feed = await ranked_feed(topic="security")         # ranked stories
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from rugbrain.dim_policy import COLLECTION_DIM
from rugbrain.news_clustering import Story, StorySource, cosine_similarity
from rugbrain.rerank_endpoint import rerank_endpoint

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

# Required config — the DeepInfra default died on 402 (see rerank_compat).
RERANKER_URL = os.getenv("RERANKER_URL", "").rstrip("/")
RERANKER_TIMEOUT = float(os.getenv("RERANKER_TIMEOUT", "30"))
NEWS_V2_COLLECTION = os.getenv("NEWS_V2_COLLECTION", "news_v2")
EMBED_DIM = int(os.getenv("RAG_EMBED_DIM", str(COLLECTION_DIM)))  # canonical lane

#: Composite score weights (must sum to 1.0).
RANK_WEIGHTS: dict[str, float] = {
    "source_credibility": 0.30,
    "factual_density": 0.20,
    "timeliness": 0.15,
    "story_corroboration": 0.20,
    "rerank_relevance": 0.15,
}

FEED_MAX_SCROLL_PAGES = 10
TRENDING_MIN_SOURCES = 2

# ── Reranking ────────────────────────────────────────────────────────────


async def rerank_story_sources(
    story: Story,
    timeout: float = 30.0,  # noqa: ASYNC109 - kept explicit for httpx client timeout
) -> tuple[list[StorySource], str]:
    """Rerank a story's source articles against its title.

    POSTs ``story.title`` + article summaries to ``RERANKER_URL`` (required,
    no baked default). The top document becomes the primary source; an unset
    or unreachable lane returns the sources by credibility
    (``method="fallback"``). Returns ``(sorted_sources, method)``.
    """
    if not story.sources:
        return [], "empty"

    documents = [src.summary[:2000] or src.title[:2000] for src in story.sources]
    try:
        async with httpx.AsyncClient(timeout=RERANKER_TIMEOUT) as client:
            resp = await client.post(
                rerank_endpoint(RERANKER_URL),
                json={
                    "query": story.title[:2000],
                    "documents": documents,
                    "top_n": len(documents),
                },
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
        # Map index -> relevance score and stamp it on each source.
        scored: dict[int, float] = {
            int(item.get("index")): float(item.get("relevance_score", 0.0))
            for item in results
            if item.get("index") is not None
        }
        ranked: list[StorySource] = []
        for idx, src in enumerate(story.sources):
            src.rerank_score = scored.get(idx, 0.0)
            ranked.append(src)
        ordered = sorted(ranked, key=lambda s: s.rerank_score, reverse=True)
        return ordered, "qwen3-reranker-8b"
    except Exception as exc:
        logger.debug("news_ranking: Qwen3 /rerank failed (%s) — credibility fallback", exc)
        for src in story.sources:
            src.rerank_score = 0.0
        ordered = sorted(story.sources, key=lambda s: s.credibility, reverse=True)
        return ordered, "credibility-fallback"


# ── Composite scoring ────────────────────────────────────────────────────


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def score_story_axes(story: Story, now: datetime | None = None) -> dict[str, float]:
    """Compute the five ranking axes for a story (0-100 each)."""
    from rugbrain.news_rating import factual_density_score, timeliness_score

    now_dt = now or datetime.now(UTC)
    sources = story.sources or []

    # 1. source credibility — mean reputation across sources.
    credibility = _clamp(_mean([float(s.credibility) for s in sources]))

    # 2. factual density — mean verifiable-claims density across summaries.
    densities = [
        factual_density_score(f"{s.title}. {s.summary}") for s in sources if s.summary or s.title
    ]
    density = _clamp(_mean([float(d) for d in densities]))

    # 3. timeliness — freshness of the newest source article.
    latest: Any = None
    for s in sources:
        parsed = _parse_dt(s.published_at)
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    freshness = timeliness_score(latest.isoformat() if latest else None, now=now_dt)

    # 4. story corroboration — how many sources cover the story.
    count = len(sources)
    corroboration = _clamp(50 + min(count, 10) * 5)  # 2 sources -> 60, 10+ -> 100

    # 5. rerank relevance — mean cross-encoder score, defaulted from consensus.
    #    Cross scores are 0-1; scale to 0-100. Without a rerank pass we use
    #    the factual consensus as a relevance proxy.
    rerank_scores = [float(s.rerank_score) for s in sources if s.rerank_score > 0]
    if rerank_scores:
        rerank = _clamp(_mean(rerank_scores) * 100)
    else:
        rerank = _clamp(story.factual_consensus * 100)

    return {
        "source_credibility": round(credibility, 1),
        "factual_density": round(density, 1),
        "timeliness": round(float(freshness), 1),
        "story_corroboration": round(corroboration, 1),
        "rerank_relevance": round(rerank, 1),
    }


def composite_story_score(story: Story, axes: dict[str, float] | None = None) -> float:
    """Weighted composite 0-100 score for a story."""
    computed = axes or score_story_axes(story)
    return round(sum(RANK_WEIGHTS[k] * computed.get(k, 0.0) for k in RANK_WEIGHTS), 1)


def story_velocity(story: Story, now: datetime | None = None) -> float:
    """Trending velocity: sources per hour since the story's first source."""
    now_dt = now or datetime.now(UTC)
    published = [
        parsed
        for s in (story.sources or [])
        if s.published_at and (parsed := _parse_dt(s.published_at)) is not None
    ]
    if not published:
        return 0.0
    first = min(published)
    hours = max((now_dt - first).total_seconds() / 3600.0, 0.01)
    return round(len(story.sources) / hours, 3)


async def rank_story(
    story: Story,
    now: datetime | None = None,
    rerank: bool = True,
) -> Story:
    """Rerank sources, compute axes + composite + velocity, set primary source.

    Mutates a copy and returns it (the input story is not modified).
    """
    updated = story.model_copy(deep=True)
    updated.updated_at = (now or datetime.now(UTC)).isoformat()
    if rerank:
        ordered, _method = await rerank_story_sources(updated)
        updated.sources = ordered
    if updated.sources:
        updated.primary_source_id = updated.sources[0].article_id
    updated.axes = score_story_axes(updated, now=now)
    updated.composite_score = composite_story_score(updated, updated.axes)
    updated.velocity = story_velocity(updated, now=now)
    return updated


# ── Qdrant persistence (news_v2) ─────────────────────────────────────────


def _story_point_id(story_id: str) -> str:
    """Deterministic Qdrant point id for a story (uuid5 over story id)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"news-story:{story_id}"))


def _story_payload(story: Story) -> dict[str, Any]:
    """Serialize a Story into a Qdrant payload (full story + facets)."""
    return {
        "type": "news_story",
        "story_id": story.id,
        "title": story.title,
        "story": story.model_dump(),
        "topics": story.topics,
        "composite_score": story.composite_score,
        "source_count": len(story.sources),
        "updated_at": story.updated_at,
    }


async def _resolve_qdrant_url() -> str:
    """Resolve the Qdrant base URL (reuse qdrant_store resolution)."""
    from rugbrain.qdrant_store import _resolve_qdrant_url as _resolve

    return await _resolve()


async def _ensure_news_v2_collection(client: httpx.AsyncClient) -> bool:
    """Create the news_v2 collection when missing (named dense vector)."""
    try:
        r = await client.get(f"/collections/{NEWS_V2_COLLECTION}")
        if r.status_code == 200:
            return True
        payload = {"vectors": {"dense": {"size": EMBED_DIM, "distance": "Cosine"}}}
        r = await client.put(f"/collections/{NEWS_V2_COLLECTION}", json=payload)
        return r.status_code in (200, 201)
    except Exception as exc:
        logger.warning("news_ranking: ensure collection failed: %s", exc)
        return False


async def _embed_title(title: str) -> list[float]:
    """Embed a story title via llama.cpp Qwen3-Embedding-8B (zero-vector fallback)."""
    try:
        from rugbrain.embeddings import get_embedding

        return await get_embedding(title)
    except Exception as exc:
        logger.debug("news_ranking: story embed failed (%s) — zero vector", exc)
        return [0.0] * EMBED_DIM


async def store_story(story: Story) -> bool:
    """Upsert a story point into the Qdrant ``news_v2`` collection.

    The vector is the story-title embedding (Qwen3-Embedding-8B) so story similarity
    search can match new articles to existing stories in real time.
    """
    try:
        vector = await _embed_title(story.title or story.summary[:200])
        url = await _resolve_qdrant_url()
        async with httpx.AsyncClient(base_url=url, timeout=30.0) as client:
            if not await _ensure_news_v2_collection(client):
                return False
            payload = {
                "points": [
                    {
                        "id": _story_point_id(story.id),
                        "vector": {"dense": vector},
                        "payload": _story_payload(story),
                    }
                ]
            }
            r = await client.put(
                f"/collections/{NEWS_V2_COLLECTION}/points",
                json=payload,
            )
            ok = r.status_code in (200, 201)
            if not ok:
                logger.warning(
                    "news_ranking: store failed status=%d body=%s",
                    r.status_code,
                    r.text[:300],
                )
            return ok
    except Exception as exc:
        logger.warning("news_ranking: store error: %s", exc)
        return False


async def load_story(story_id: str) -> Story | None:
    """Load a single story by id (None when missing/unreachable)."""
    try:
        url = await _resolve_qdrant_url()
        async with httpx.AsyncClient(base_url=url, timeout=15.0) as client:
            r = await client.get(
                f"/collections/{NEWS_V2_COLLECTION}/points/{_story_point_id(story_id)}",
                params={"with_payload": True, "with_vector": False},
            )
            if r.status_code != 200:
                return None
            payload = r.json().get("result", {}).get("payload", {})
            story_data = payload.get("story")
            if not isinstance(story_data, dict):
                return None
            return Story(**story_data)
    except Exception as exc:
        logger.warning("news_ranking: load_story(%s) failed: %s", story_id, exc)
        return None


async def scroll_stories(
    limit: int = 100,
    offset: str | None = None,
    topic: str | None = None,
    min_quality: float = 0.0,
) -> tuple[list[Story], str | None]:
    """Scroll story points from Qdrant ``news_v2``.

    Filters to ``type=news_story`` points, optionally narrowing by topic
    (payload ``topics`` array contains *topic*) and ``composite_score``.
    Returns ``(stories, next_offset)``.
    """

    try:
        url = await _resolve_qdrant_url()
        async with httpx.AsyncClient(base_url=url, timeout=20.0) as client:
            if not await _ensure_news_v2_collection(client):
                return [], None
            must: list[dict[str, Any]] = [{"key": "type", "match": {"value": "news_story"}}]
            if topic:
                must.append({"key": "topics", "match": {"value": topic}})
            if min_quality > 0:
                must.append({"key": "composite_score", "range": {"gte": min_quality}})
            body: dict[str, Any] = {
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
                "filter": {"must": must},
            }
            if offset:
                body["offset"] = offset
            r = await client.post(
                f"/collections/{NEWS_V2_COLLECTION}/points/scroll",
                json=body,
            )
            if r.status_code != 200:
                return [], None
            data = r.json().get("result", {}) or {}
            stories: list[Story] = []
            for point in data.get("points", []):
                payload = point.get("payload", {}) or {}
                story_data = payload.get("story")
                if isinstance(story_data, dict):
                    stories.append(Story(**story_data))
            next_offset = data.get("next_page_offset")
            return stories, next_offset
    except Exception as exc:
        logger.warning("news_ranking: scroll_stories failed: %s", exc)
        return [], None


async def ranked_feed(
    topic: str | None = None,
    min_quality: float = 70.0,
    limit: int = 20,
) -> list[Story]:
    """Ranked news feed: stories sorted by composite score descending.

    Optionally filtered by *topic* and a minimum ``composite_score``.
    Fails open to an empty list when Qdrant is unreachable.
    """
    collected: list[Story] = []
    offset: str | None = None
    for _ in range(FEED_MAX_SCROLL_PAGES):
        stories, offset = await scroll_stories(
            limit=min(100, max(limit * 4, 100)),
            offset=offset,
            topic=topic,
            min_quality=min_quality,
        )
        if not stories:
            break
        collected.extend(stories)
        if not offset:
            break
    collected.sort(key=lambda s: s.composite_score, reverse=True)
    return collected[:limit]


async def trending_stories(limit: int = 20, now: datetime | None = None) -> list[Story]:
    """Trending stories ranked by velocity (sources/hour), min 2 sources."""
    stories: list[Story] = []
    offset: str | None = None
    for _ in range(FEED_MAX_SCROLL_PAGES):
        batch, offset = await scroll_stories(limit=100, offset=offset)
        if not batch:
            break
        stories.extend(batch)
        if not offset:
            break
    now_dt = now or datetime.now(UTC)
    for story in stories:
        story.velocity = story_velocity(story, now=now_dt)
    stories = [s for s in stories if len(s.sources) >= TRENDING_MIN_SOURCES]
    stories.sort(key=lambda s: s.velocity, reverse=True)
    return stories[:limit]


async def topic_counts(limit: int = 50) -> dict[str, int]:
    """Count stories per topic (aggregated from Qdrant payloads)."""
    counts: dict[str, int] = {}
    offset: str | None = None
    for _ in range(FEED_MAX_SCROLL_PAGES):
        batch, offset = await scroll_stories(limit=100, offset=offset)
        if not batch:
            break
        for story in batch:
            for topic in story.topics:
                counts[topic] = counts.get(topic, 0) + 1
        if not offset:
            break
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit])


async def find_matching_story(
    title: str,
    embedding: list[float],
    threshold: float,
) -> Story | None:
    """Find the best existing story for a new article (by title similarity).

    Scrolls story points with their vectors and returns the story whose
    title embedding is most similar to *embedding* above *threshold*.
    """
    try:
        from rugbrain.qdrant_store import build_qdrant_filter

        url = await _resolve_qdrant_url()
        async with httpx.AsyncClient(base_url=url, timeout=20.0) as client:
            if not await _ensure_news_v2_collection(client):
                return None
            body: dict[str, Any] = {
                "limit": 200,
                "with_payload": True,
                "with_vector": True,
                "filter": build_qdrant_filter({"type": "news_story"})
                or {"must": [{"key": "type", "match": {"value": "news_story"}}]},
            }
            r = await client.post(
                f"/collections/{NEWS_V2_COLLECTION}/points/scroll",
                json=body,
            )
            if r.status_code != 200:
                return None
            best: Story | None = None
            best_sim = threshold
            for point in (r.json().get("result", {}) or {}).get("points", []):
                vector = (point.get("vector") or {}).get("dense")
                if not vector:
                    continue
                sim = cosine_similarity(embedding, [float(v) for v in vector])
                if sim >= best_sim:
                    payload = point.get("payload", {}) or {}
                    story_data = payload.get("story")
                    if isinstance(story_data, dict):
                        best = Story(**story_data)
                        best_sim = sim
            return best
    except Exception as exc:
        logger.warning("news_ranking: find_matching_story failed: %s", exc)
        return None


__all__ = [
    "RANK_WEIGHTS",
    "composite_story_score",
    "load_story",
    "rank_story",
    "ranked_feed",
    "rerank_story_sources",
    "score_story_axes",
    "scroll_stories",
    "store_story",
    "story_velocity",
    "topic_counts",
    "trending_stories",
]
