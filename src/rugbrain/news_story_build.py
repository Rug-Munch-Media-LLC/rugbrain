"""Story assembly: id, title, summary and the Story object.

Split out of :mod:`rugbrain.news_clustering` (2026-10-25, size ratchet).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from rugbrain.news_cluster_core import MAX_SUMMARY_CHARS, Story, StorySource
from rugbrain.news_story_consensus import _factual_consensus
from rugbrain.news_story_metrics import _bias_distribution, _extract_topics, _sentiment_distribution
from rugbrain.news_story_sources import (
    _build_timeline,
    _parse_dt,
    _sources_from_articles,
)


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
