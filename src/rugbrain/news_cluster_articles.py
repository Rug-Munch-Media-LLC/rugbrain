"""Article-level clustering entry point (embeddings -> stories).

Split out of :mod:`rugbrain.news_clustering` (2026-10-25, size ratchet).
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.news_cluster_core import (
    CLUSTER_THRESHOLD,
    Embedder,
    Story,
    cluster_embeddings,
    embed_titles,
)
from rugbrain.news_story_build import _article_sort_key, build_story

logger = logging.getLogger(__name__)


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
        llama.cpp Qwen3-Embedding-8B via :func:`embed_titles`. Inject for tests.

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
