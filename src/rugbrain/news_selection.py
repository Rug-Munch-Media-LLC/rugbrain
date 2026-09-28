"""Article selection for `GET /api/v1/news/top`.

Split out of :mod:`rugbrain.routes.intel` to keep that module inside the
200-line limit, and because the selection policy is worth stating in one place.

Ranking uses the best quality signal a point actually carries:

1. ``metadata.rating`` — the ``news_rating`` composite, when the rating engine
   has run for that point. Subject to the caller's ``min_quality`` threshold.
2. ``metadata.importance`` — the Telegram ingester's own keyword-weight score.
   Observed on ``news_v2``: p50 0.0, p90 1.05, max 2.80.

``min_quality`` deliberately does **not** apply to path 2. It is a threshold on
a 0..100 rating scale; applying it to a 0..2.8 signal is precisely what made
this endpoint return an empty list for every request while looking healthy.
Every returned article reports which signal ranked it, so a consumer can tell
rated content from heuristic-ranked content.
"""

from __future__ import annotations

from typing import Any

#: Maps `metadata.importance` onto the 0..100 quality band (observed max ~2.8).
IMPORTANCE_FULL_SCALE = 2.8

#: Noise floor for the heuristic path — below this an item is not worth surfacing.
IMPORTANCE_MIN = 0.5

#: The field every stored `news_v2`/`news_articles` point is filtered on first.
COLLECTION = "news_v2"


def rate_article_from_rating_dict(rating: dict[str, Any]) -> dict[str, Any]:
    """Delegate to the rating engine's dict adapter, tolerating its absence."""
    try:
        from rugbrain.news_rating import rate_article_from_rating_dict as _rate

        return _rate(rating)
    except Exception:
        return {"quality_score": 0, "tier": "fair", "factuality": "reported", "axes": {}}


def _heuristic_quality(importance: float) -> int:
    """Rescale the ingester's importance score into the 0..100 quality band."""
    return min(100, round(importance * (100 / IMPORTANCE_FULL_SCALE)))


def select_articles(
    points: list[dict[str, Any]],
    *,
    category: str | None = None,
    min_quality: int = 70,
) -> list[dict[str, Any]]:
    """Rank `points` and return the qualifying articles, best first.

    Args:
        points: scroll results; each has ``id``, ``metadata`` and optional
            ``source``.
        category: case-insensitive exact match on ``metadata.category``.
        min_quality: threshold applied to the rating path only.
    """
    articles: list[dict[str, Any]] = []
    for result in points:
        metadata = result.get("metadata") or {}
        if category and str(metadata.get("category", "")).lower() != str(category).lower():
            continue
        rating = metadata.get("rating")
        if isinstance(rating, dict):
            payload = rate_article_from_rating_dict(rating)
            quality = int(payload.get("quality_score", 0))
            ranked_by = "rating"
            if quality < min_quality:
                continue
        else:
            raw = metadata.get("importance")
            if not isinstance(raw, (int, float)) or float(raw) < IMPORTANCE_MIN:
                continue
            quality = _heuristic_quality(float(raw))
            payload = {"quality_score": quality, "tier": "heuristic", "axes": {}}
            ranked_by = "importance"
        articles.append(
            {
                "id": result.get("id"),
                "headline": _headline(result),
                "title": metadata.get("title", ""),
                "source": metadata.get("source", result.get("source", "")),
                "url": metadata.get("url", ""),
                "published_at": metadata.get("published_at", ""),
                "category": metadata.get("category", ""),
                "quality": quality,
                "ranked_by": ranked_by,
                "tier": payload.get("tier", "fair"),
                "factuality": payload.get("factality", "reported"),
                "axes": payload.get("axes", {}),
                "rating": payload if ranked_by == "rating" else None,
            }
        )
    articles.sort(key=lambda article: article["quality"], reverse=True)
    return articles


def _headline(result: dict[str, Any]) -> str:
    """Plain-text, emoji-styled headline for the site (never Telegram HTML)."""
    try:
        from rugbrain.content_story import article_headline

        return article_headline(result)
    except Exception:
        return str((result.get("metadata") or {}).get("title", ""))
