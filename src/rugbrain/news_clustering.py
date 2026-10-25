"""News clustering — group news articles into deduplicated stories.

Turns per-article records into :class:`Story` objects: title-embedding
clustering, source aggregation, timeline/sentiment/bias distributions,
topic extraction and factual-consensus scoring.

Split into cohesive modules on 2026-10-25 (size ratchet); this module stays
the stable import surface:

    news_cluster_core       models, embedder seam, cosine + clustering
    news_story_sources      source extraction, timeline
    news_story_metrics      sentiment/bias distributions, topics
    news_story_consensus    factual consensus
    news_story_build        story id/title/summary, build_story
    news_cluster_articles   cluster_articles entry point
"""

from __future__ import annotations

from rugbrain.news_cluster_articles import cluster_articles
from rugbrain.news_cluster_core import (
    CLUSTER_THRESHOLD,
    EMBED_DIM,
    MAX_STORY_SOURCES,
    Embedder,
    Story,
    StorySource,
    cluster_embeddings,
    cosine_similarity,
    embed_titles,
)
from rugbrain.news_story_build import build_story
from rugbrain.news_story_sources import _parse_dt as _parse_dt

__all__ = [
    "CLUSTER_THRESHOLD",
    "EMBED_DIM",
    "MAX_STORY_SOURCES",
    "Embedder",
    "Story",
    "StorySource",
    "_parse_dt",
    "build_story",
    "cluster_articles",
    "cluster_embeddings",
    "cosine_similarity",
    "embed_titles",
]
