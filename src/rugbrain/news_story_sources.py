"""Story source extraction and timeline helpers.

Split out of :mod:`rugbrain.news_clustering` (2026-10-25, size ratchet).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rugbrain.news_cluster_core import StorySource


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
