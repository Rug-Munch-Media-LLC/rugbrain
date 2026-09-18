"""Source diversity: distinct-source counting and scoring."""

from __future__ import annotations

from typing import Any


def _distinct_sources(article: dict[str, Any]) -> int:
    """Count distinct source references in the article dict.

    Recognises ``source_urls``, ``references``, ``related_articles`` and
    ``citations`` (each list of URLs/names). An ``aggregated`` flag counts
    as at least three independent sources.
    """
    seen: set[str] = set()
    for key in ("source_urls", "references", "related_articles", "citations"):
        for item in article.get(key) or []:
            if isinstance(item, str) and item.strip():
                seen.add(item.strip())
            elif isinstance(item, dict):
                for sub in (item.get("url"), item.get("source"), item.get("title")):
                    if isinstance(sub, str) and sub.strip():
                        seen.add(sub.strip())
    count = len(seen)
    if article.get("aggregated"):
        count = max(count, 3)
    return count


def source_diversity_score(article: dict[str, Any]) -> int:
    """0-100: multi-source / aggregated reporting scores higher."""
    count = _distinct_sources(article)
    if count == 0:
        return 50  # unknown — typical single-source article
    if count == 1:
        return 40
    if count == 2:
        return 60
    if count == 3:
        return 75
    return 90
