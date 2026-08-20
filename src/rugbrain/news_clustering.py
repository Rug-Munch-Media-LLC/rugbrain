"""
News story clustering — group related news articles into "stories".

Ground News / CryptoPanic-style story detection for RugBrain.

Articles are embedded with ``/v1/embeddings`` via self-hosted Qwen3-Embedding-4B (2560-dim) and grouped
with a greedy cosine-similarity centroid clusterer. Every resulting cluster
is a single *story*: a title, a summary, the source articles (each with a
credibility score and editorial leaning), a timeline, a sentiment
distribution and a bias distribution.

Pipeline
--------
1. ``embed_titles()`` — batch-embed article titles via Qwen3-Embedding-4B (2560-dim).
2. ``cluster_embeddings()`` — greedy single-pass clustering with centroid
   tracking; returns a list of article-index groups.
3. ``build_story()`` — enrich one cluster into a ``Story`` (sources,
   timeline, sentiment/bias distribution, factual consensus, topics).
4. ``cluster_articles()`` — orchestrate the full batch pipeline.

The module is deterministic given the same inputs (articles sorted newest
first so the freshest article anchors each cluster) and is network-free
except for the embedding step, which callers can inject for tests.

Usage::

    from rugbrain.news_clustering import cluster_articles

    stories = await cluster_articles([article_dict, ...])
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

#: Default cosine threshold for grouping article titles into one story.
#: Tuned for Qwen3-Embedding-4B 2560-dim title embeddings (same-story titles typically
#: land 0.78+; distinct stories usually < 0.70).
CLUSTER_THRESHOLD = float(os.getenv("NEWS_CLUSTER_THRESHOLD", "0.72"))

#: Embedding model dimension (Qwen3-Embedding-4B). Aligned with rugbrain.embeddings.
EMBED_DIM = 2560

#: Max sources kept in a single story (guard against runaway clusters).
MAX_STORY_SOURCES = int(os.getenv("NEWS_MAX_STORY_SOURCES", "25"))

#: Max summary characters retained on a story.
MAX_SUMMARY_CHARS = int(os.getenv("NEWS_MAX_SUMMARY_CHARS", "600"))

#: Embedder signature: list[str] -> list[list[float]] (async).
Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


# ── Models ───────────────────────────────────────────────────────────────


class StorySource(BaseModel):
    """One source article that reports a story."""

    article_id: str = ""
    title: str = ""
    url: str = ""
    source: str = ""
    domain: str = ""
    credibility: int = 0  # 0-100 source reputation
    tier: str = "unknown"  # established | independent | unknown
    leaning: str = "unknown"  # left | center | right | unknown
    published_at: str | None = None
    summary: str = ""
    sentiment: str = "neutral"  # positive | neutral | negative
    rerank_score: float = 0.0  # cross-encoder relevance vs story title (0-1)


class Story(BaseModel):
    """A clustered news story with multiple source articles."""

    id: str = ""
    title: str = ""
    summary: str = ""
    sources: list[StorySource] = Field(default_factory=list)
    primary_source_id: str = ""
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    sentiment_distribution: dict[str, int] = Field(default_factory=dict)
    bias_distribution: dict[str, int] = Field(default_factory=dict)
    factual_consensus: float = 0.5  # 0-1 agreement between sources
    consensus_detail: dict[str, Any] = Field(default_factory=dict)
    topics: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    # Ranking fields (filled by rugbrain.news_ranking).
    axes: dict[str, float] = Field(default_factory=dict)
    composite_score: float = 0.0
    velocity: float = 0.0  # sources/hour — trending signal


# ── Embedding ────────────────────────────────────────────────────────────


async def embed_titles(titles: list[str]) -> list[list[float]]:
    """Embed article titles with llama.cpp ``Qwen3-Embedding-4B`` (2560-dim).

    Delegates to ``rugbrain.embeddings.get_embeddings`` (the canonical
    embedder used across RugBrain). Fails loud when no backend is
    available — clustering should never silently produce garbage vectors.
    """
    from rugbrain.embeddings import get_embeddings

    return await get_embeddings(titles)


# ── Similarity + clustering ──────────────────────────────────────────────


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors (0.0 on empty)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    denominator = (norm_a**0.5) * (norm_b**0.5)
    return float(dot / denominator) if denominator else 0.0


def cluster_embeddings(
    embeddings: list[list[float]],
    threshold: float = CLUSTER_THRESHOLD,
) -> list[list[int]]:
    """Greedy single-pass clustering of embeddings by cosine similarity.

    Each item joins the existing cluster whose centroid is most similar to
    it; when the best similarity is below *threshold* a new cluster starts.
    Centroids are running means, so cluster order is stable for a given
    input order (callers sort articles newest-first beforehand).

    Returns a list of clusters, each a list of input indices.
    """
    clusters: list[list[int]] = []
    centroids: list[list[float]] = []
    for idx, emb in enumerate(embeddings):
        best_sim = -1.0
        best_cluster = -1
        for cidx, centroid in enumerate(centroids):
            sim = cosine_similarity(emb, centroid)
            if sim > best_sim:
                best_sim = sim
                best_cluster = cidx
        if best_cluster >= 0 and best_sim >= threshold:
            cluster = clusters[best_cluster]
            cluster.append(idx)
            n = len(cluster)
            centroid = centroids[best_cluster]
            centroids[best_cluster] = [
                (centroid[d] * (n - 1) + emb[d]) / n for d in range(len(emb))
            ]
        else:
            clusters.append([idx])
            centroids.append(list(emb))
    return clusters


# ── Story building ───────────────────────────────────────────────────────


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


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


def _sources_from_articles(
    articles: list[dict[str, Any]],
    story_id: str,
) -> list[StorySource]:
    """Map raw article dicts to StorySource entries with credibility/leaning."""
    from rugbrain.news_rating import domain_from_url, source_reputation_score
    from rugbrain.news_sources import EXTENDED_SOURCE_LEANINGS
    from rugbrain.sentiment_engine import analyze_text_sentiment

    sources: list[StorySource] = []
    for article in articles:
        url = str(article.get("url") or "")
        domain = domain_from_url(url)
        source_name = str(article.get("source") or "")
        credibility = source_reputation_score(source_name, url)
        title = str(article.get("title") or "").strip()
        summary = str(article.get("summary") or article.get("content") or "")[:2000]
        text = f"{title}. {summary}" if title else summary
        sentiment_result = analyze_text_sentiment(text)
        sentiment = sentiment_result.sentiment  # bearish | bullish | neutral
        leaning = EXTENDED_SOURCE_LEANINGS.get(domain, "unknown")

        sources.append(
            StorySource(
                article_id=str(article.get("id") or ""),
                title=title,
                url=url,
                source=source_name,
                domain=domain,
                credibility=credibility,
                tier=(
                    "established"
                    if credibility >= 75
                    else "independent"
                    if credibility >= 50
                    else "unknown"
                ),
                leaning=leaning,
                published_at=(
                    article.get("published_at") if article.get("published_at") is not None else None
                ),
                summary=summary[:800],
                sentiment=sentiment,
            )
        )
    return sources


def _build_timeline(sources: list[StorySource]) -> list[dict[str, Any]]:
    """Chronological timeline of the story's sources."""
    timeline = [
        {
            "published_at": src.published_at,
            "title": src.title,
            "source": src.source,
            "url": src.url,
            "article_id": src.article_id,
        }
        for src in sources
    ]
    timeline.sort(
        key=lambda item: _parse_dt(item["published_at"]) or datetime.min.replace(tzinfo=UTC)
    )
    return timeline


def _sentiment_distribution(sources: list[StorySource]) -> dict[str, int]:
    """Count sources by sentiment (positive/neutral/negative)."""
    dist = {"positive": 0, "neutral": 0, "negative": 0}
    for src in sources:
        # sentiment_engine returns bearish/bullish/neutral — map to the
        # story-facing vocabulary.
        key = {
            "bullish": "positive",
            "bearish": "negative",
            "neutral": "neutral",
        }.get(src.sentiment, "neutral")
        dist[key] = dist.get(key, 0) + 1
    return dist


def _bias_distribution(sources: list[StorySource]) -> dict[str, int]:
    """Count sources by editorial leaning (left/center/right/unknown)."""
    dist: dict[str, int] = {"left": 0, "center": 0, "right": 0, "unknown": 0}
    for src in sources:
        leaning = src.leaning if src.leaning in dist else "unknown"
        dist[leaning] += 1
    return dist


# Protocol names reused for topic extraction (keep in sync with news_rating).
_TOPIC_PROTOCOL_RE = re.compile(
    r"\b(uniswap|aave|compound|curve|lido|makerdao|eigenlayer|arbitrum|optimism|"
    r"polygon|solana|ethereum|bitcoin|tether|chainlink|pyth|wormhole|layerzero|"
    r"hyperliquid|tornado cash|euler|mango|platypus|radiant|venus|benqi|cream|"
    r"terra|luna|ftx|alameda|celsius|blockfi|voyager|genesis|ronin|axie|nomad|"
    r"wintermute|jupiter|raydium|metaplex|opensea|blur|balancer|sushiswap|"
    r"pancakeswap|dydx|synthetix|yearn|convex|rocket pool)\b",
    re.IGNORECASE,
)


def _extract_topics(articles: list[dict[str, Any]], sources: list[StorySource]) -> list[str]:
    """Derive story topics from article categories + protocols mentioned."""
    topics: set[str] = set()
    for article in articles:
        category = str(article.get("category") or "").strip().lower()
        if category and category != "crypto":
            topics.add(category)
        for t in article.get("topics") or []:
            if isinstance(t, str) and t.strip():
                topics.add(t.strip().lower())
    for src in sources:
        text = f"{src.title} {src.summary}"
        for m in _TOPIC_PROTOCOL_RE.finditer(text):
            topics.add(m.group(1).lower())
    # Deterministic order — largest to smallest, then alpha.
    return sorted(topics, key=lambda t: (-len(t), t))[:8]


def _factual_consensus(sources: list[StorySource]) -> tuple[float, dict[str, Any]]:
    """Estimate agreement between sources on verifiable facts (0-1).

    Extracts verifiable claims (addresses, tx hashes, amounts, percentages,
    protocols) from each source summary and compares them across sources.
    A story where the same claims recur in many sources has high consensus;
    contradictory / single-source claims lower it.

    Returns ``(consensus, detail)``. ``consensus=0.5`` when there are no
    verifiable claims to compare (neutral — can't disagree on nothing).
    """
    from rugbrain.news_rating import _find_claims

    claim_sets: list[set[tuple[str, str]]] = []
    total_sources_with_claims = 0
    for src in sources:
        text = f"{src.title}. {src.summary}"
        claims = _find_claims(text)
        flat: set[tuple[str, str]] = set()
        for category, values in claims.items():
            for value in values:
                flat.add((category, str(value).lower()))
        claim_sets.append(flat)
        if flat:
            total_sources_with_claims += 1

    if not claim_sets:
        return 0.5, {"method": "no-claims", "sources_with_claims": 0}

    # Union of all claims across sources; count how many sources support each.
    all_claims: dict[tuple[str, str], int] = {}
    for cs in claim_sets:
        for claim in cs:
            all_claims[claim] = all_claims.get(claim, 0) + 1

    if not all_claims:
        return 0.5, {"method": "no-claims", "sources_with_claims": total_sources_with_claims}

    supported = sum(1 for count in all_claims.values() if count >= 2)
    total = len(all_claims)
    consensus = supported / total if total else 0.5
    # Weight consensus toward stories corroborated by multiple sources.
    if total_sources_with_claims >= 3:
        consensus = _clamp(consensus * 0.9 + 0.1)
    detail = {
        "method": "claim-overlap",
        "sources_with_claims": total_sources_with_claims,
        "total_unique_claims": total,
        "multi_source_claims": supported,
        "single_source_claims": total - supported,
        "sample_claims": sorted({c[1] for c in all_claims})[:10],
    }
    return round(consensus, 4), detail


def _story_id(articles: list[dict[str, Any]]) -> str:
    """Deterministic story id: uuid5 over the sorted set of source URLs.

    Stable across re-runs (same sources -> same story) so real-time updates
    can find and extend an existing story instead of duplicating it.
    """
    urls = sorted(
        {str(a.get("url") or "") for a in articles} | {str(a.get("title") or "") for a in articles}
    )
    namespace = uuid.NAMESPACE_URL
    return str(uuid.uuid5(namespace, "|".join(urls)))


def _pick_title(articles: list[dict[str, Any]], sources: list[StorySource]) -> str:
    """Choose the story title: highest-credibility source's title wins."""
    if not articles:
        return ""
    best_index = 0
    best_cred = -1
    for i, src in enumerate(sources):
        if src.credibility > best_cred:
            best_cred = src.credibility
            best_index = i
    return sources[best_index].title or str(articles[best_index].get("title") or "")


def _build_summary(articles: list[dict[str, Any]], sources: list[StorySource]) -> str:
    """Compose a story summary from the top-credibility source summaries."""
    ordered = sorted(sources, key=lambda s: s.credibility, reverse=True)
    parts: list[str] = []
    for src in ordered[:2]:
        text = src.summary.strip()
        if text and text not in parts:
            parts.append(text)
    joined = " ".join(parts).strip()
    if not joined:
        joined = " ".join(
            str(a.get("summary") or a.get("content") or "").strip() for a in articles[:1]
        ).strip()
    if not joined:
        joined = " ".join(s.title for s in sources[:2]).strip()
    return joined[:MAX_SUMMARY_CHARS]


def build_story(articles: list[dict[str, Any]], story_id: str | None = None) -> Story:
    """Build a ``Story`` from a cluster of raw article dicts.

    Fills sources (with credibility + leaning), timeline, sentiment and
    bias distributions, factual consensus and topics. Ranking (rerank +
    composite score) is applied later by ``rugbrain.news_ranking``.
    """
    sources = _sources_from_articles(articles, story_id or "")
    if not sources:
        return Story(id=story_id or "", title="", summary="")
    sid = story_id or _story_id(articles)
    title = _pick_title(articles, sources)
    summary = _build_summary(articles, sources)
    consensus, consensus_detail = _factual_consensus(sources)
    topics = _extract_topics(articles, sources)
    now = datetime.now(UTC).isoformat()
    return Story(
        id=sid,
        title=title,
        summary=summary,
        sources=sources,
        timeline=_build_timeline(sources),
        sentiment_distribution=_sentiment_distribution(sources),
        bias_distribution=_bias_distribution(sources),
        factual_consensus=consensus,
        consensus_detail=consensus_detail,
        topics=topics,
        created_at=now,
        updated_at=now,
    )


# ── Orchestration ────────────────────────────────────────────────────────


def _article_sort_key(article: dict[str, Any]) -> tuple[int, str]:
    """Sort key: newest article first, then by title for stability."""
    parsed = _parse_dt(article.get("published_at"))
    ts = int(parsed.timestamp()) if parsed else 0
    return (-ts, str(article.get("title") or ""))


async def cluster_articles(
    articles: list[dict[str, Any]],
    threshold: float = CLUSTER_THRESHOLD,
    embedder: Embedder | None = None,
) -> list[Story]:
    """Cluster raw article dicts into stories.

    Parameters
    ----------
    articles:
        List of article dicts with ``title`` (embedded), ``url``,
        ``source``, ``summary``/``content``, ``published_at``,
        ``category`` and optional ``id``/``topics``.
    threshold:
        Cosine similarity threshold for grouping titles into a story.
    embedder:
        Async ``list[str] -> list[list[float]]`` callable; defaults to
        llama.cpp Qwen3-Embedding-4B via :func:`embed_titles`. Inject for tests.

    Returns
    -------
    list[Story]
        One story per detected cluster (empty when *articles* is empty).
    """
    if not articles:
        return []
    ordered = sorted(articles, key=_article_sort_key)
    titles = [str(a.get("title") or "") for a in ordered]
    embed = embedder or embed_titles
    embeddings = await embed(titles)
    if len(embeddings) != len(ordered):
        logger.warning(
            "news_cluster: embedder returned %d vectors for %d titles",
            len(embeddings),
            len(ordered),
        )
        embeddings = embeddings[: len(ordered)]

    clusters = cluster_embeddings(embeddings, threshold=threshold)
    stories: list[Story] = []
    for cluster in clusters:
        cluster_articles_raw = [ordered[i] for i in cluster]
        story = build_story(cluster_articles_raw)
        if story.sources:
            stories.append(story)
    logger.info(
        "news_cluster: %d articles -> %d stories (threshold=%.2f)",
        len(ordered),
        len(stories),
        threshold,
    )
    return stories


__all__ = [
    "CLUSTER_THRESHOLD",
    "EMBED_DIM",
    "MAX_STORY_SOURCES",
    "Embedder",
    "Story",
    "StorySource",
    "build_story",
    "cluster_articles",
    "cluster_embeddings",
    "cosine_similarity",
    "embed_titles",
]
