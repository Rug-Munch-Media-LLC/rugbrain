"""Temporal Decay Indexing — time-aware relevance weighting for search results.

Applies an exponential decay factor to search result scores based on how
recent the content is. Recent content (hours/days) gets a boost; older
content (weeks/months) decays naturally.

Formula::
    decayed_score = original_score * (1.0 + boost_factor * e^(-age/decay_halflife))

Where:
    - ``age`` = hours since the content's ``ingested_at`` or ``published_at`` timestamp
    - ``decay_halflife_hours`` = how fast the boost decays (default: 24h = 1 day)
    - ``boost_factor`` = max multiplier for brand-new content (default: 0.5 = 50% boost)
    - Content older than ``max_age_hours`` is not penalized (score = original)
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

# Default decay parameters
DECAY_HALFLIFE_HOURS = 24.0  # 1 day halflife
BOOST_FACTOR = 0.5  # 50% max boost for brand-new content
MAX_AGE_HOURS = 720.0  # 30 days — content older than this gets no boost/penalty

# Timestamp field keys to check (in priority order)
TIMESTAMP_KEYS = [
    "ingested_at",
    "published_at",
    "scanned_at",
    "created_at",
    "timestamp",
    "date",
    "block_timestamp",
]


def _parse_timestamp(value: Any) -> float | None:
    """Parse a timestamp value to Unix epoch seconds.

    Handles ISO 8601 strings, Unix timestamps (int/float), and datetime objects.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        # Try ISO 8601
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, TypeError):
            pass
        # Try Unix timestamp string
        try:
            return float(value)
        except (ValueError, TypeError):
            pass
    return None


def _get_timestamp(metadata: dict[str, Any]) -> float | None:
    """Extract the best timestamp from result metadata."""
    for key in TIMESTAMP_KEYS:
        # Check metadata directly
        ts = _parse_timestamp(metadata.get(key))
        if ts is not None:
            return ts
        # Check nested (e.g., metadata.get("metadata", {}).get("timestamp"))
        if isinstance(metadata.get("metadata"), dict):
            ts = _parse_timestamp(metadata["metadata"].get(key))
            if ts is not None:
                return ts
    return None


def apply_temporal_decay(
    results: list[dict[str, Any]],
    decay_halflife: float = DECAY_HALFLIFE_HOURS,
    boost_factor: float = BOOST_FACTOR,
    max_age: float = MAX_AGE_HOURS,
    score_key: str = "score",
) -> list[dict[str, Any]]:
    """Apply temporal decay to a list of search results.

    Modifies the ``score_key`` field in each result dict in-place and returns
    the list sorted by the new decayed score (descending).

    Args:
        results: List of result dicts, each with a ``score`` field and
                 optionally a ``metadata`` dict with timestamp fields.
        decay_halflife: Hours for the exponential boost to halve (default 24h).
        boost_factor: Maximum score multiplier for brand-new content (default 0.5).
        max_age: Hours after which no boost/penalty is applied (default 720h = 30d).
        score_key: The key in each result dict containing the relevance score.

    Returns:
        The same list, sorted by decayed score descending.
    """
    now = time.time()

    for result in results:
        original_score = result.get(score_key, 0.0)
        if original_score <= 0.0:
            continue

        ts = _get_timestamp(result)
        if ts is None:
            # No timestamp — keep original score
            continue

        age_hours = (now - ts) / 3600.0
        if age_hours < 0:
            age_hours = 0.0

        if age_hours >= max_age:
            # Content is old enough that no boost/penalty applies
            continue

        # Exponential decay: boost = boost_factor * 2^(-age/decay_halflife)
        decay = math.pow(2.0, -age_hours / decay_halflife)
        boost = boost_factor * decay
        result[score_key] = round(original_score * (1.0 + boost), 6)
        result["_temporal_boost"] = round(boost, 4)
        result["_age_hours"] = round(age_hours, 1)

    # Sort by decayed score descending
    results.sort(key=lambda r: r.get(score_key, 0.0), reverse=True)
    return results


def apply_temporal_decay_batch(
    results_by_collection: dict[str, list[dict[str, Any]]],
    **kwargs: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Apply temporal decay to results from multiple collections.

    ``results_by_collection`` is a dict mapping collection names to lists of
    result dicts. Returns the same structure with decay applied per-collection.
    """
    for collection, results in results_by_collection.items():
        apply_temporal_decay(results, **kwargs)
    return results_by_collection
