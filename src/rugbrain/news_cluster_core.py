"""News clustering core: models, the embedder seam and title clustering.

Split out of :mod:`rugbrain.news_clustering` (2026-10-25, size ratchet).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from rugbrain.dim_policy import COLLECTION_DIM

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

#: Default cosine threshold for grouping article titles into one story.
#: Same-story titles land 0.78+, distinct stories < 0.70. A cosine ratio does
#: not depend on width, so the 2560->4096 lane switch left the value valid.
CLUSTER_THRESHOLD = float(os.getenv("NEWS_CLUSTER_THRESHOLD", "0.72"))
#: Embedding dim, from the canonical lane policy (not hardcoded 2560, the
#: retired 4B generation) so it cannot drift from the emitted vectors.
EMBED_DIM = int(os.getenv("RAG_EMBED_DIM", str(COLLECTION_DIM)))

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
    """Embed article titles with llama.cpp ``Qwen3-Embedding-8B`` (2560-dim).

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
