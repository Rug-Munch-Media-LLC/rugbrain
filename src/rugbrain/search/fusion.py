"""Recency-aware RRF fusion helpers.

Additive retrieval-quality signal: a half-life decay on ``published`` / date
metadata, folded into the RRF weight so fresher documents rank above
equally-relevant stale ones.

All helpers default OFF — callers opt in via ``use_recency_decay`` /
``rrf_fuse_recency``. ``apply_recency_decay`` post-multiplies each doc's
fused score by its decay factor, which is equivalent to applying the factor
inside the per-list RRF term (the decay is per-doc and the RRF score is a
per-doc sum of list contributions). Docs without a parseable date are left
unchanged (decay factor 1.0).
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any

from rugbrain.rrf_fusion import _rrf_fuse

logger = logging.getLogger(__name__)

# Metadata / top-level keys checked (in order) when looking for a doc date.
_DATE_KEYS = ("published", "published_at", "date", "timestamp", "created_at", "stored_at")


def half_life_decay(age_days: float, half_life_days: float) -> float:
    """Exponential half-life decay: 1.0 at age 0, 0.5 at age == half-life."""
    if half_life_days <= 0 or age_days <= 0:
        return 1.0
    return math.pow(0.5, age_days / half_life_days)


def _parse_date(value: Any) -> datetime | None:
    """Best-effort parse of *value* into a timezone-aware datetime.

    Supports ``datetime``, epoch seconds / millis (int/float or numeric
    string), and ISO 8601 strings (with or without timezone; naive values
    are assumed UTC).
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 1e12:  # epoch millis
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromtimestamp(float(text), tz=UTC)
        except (OverflowError, OSError, ValueError):
            pass
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return None
    return None


def extract_doc_date(doc: dict[str, Any], date_field: str = "published") -> datetime | None:
    """Best-effort date extraction from a result dict (metadata first).

    Checks ``date_field`` first, then the standard ``_DATE_KEYS``, in both
    the ``metadata`` sub-dict and the top-level result dict. Returns the
    first parseable date, or ``None``.
    """
    keys = (date_field, *_DATE_KEYS)
    candidates: list[Any] = []
    metadata = doc.get("metadata")
    if isinstance(metadata, dict):
        for key in keys:
            val = metadata.get(key)
            if val not in (None, ""):
                candidates.append(val)
    for key in keys:
        val = doc.get(key)
        if val not in (None, ""):
            candidates.append(val)
    for candidate in candidates:
        dt = _parse_date(candidate)
        if dt is not None:
            return dt
    return None


def apply_recency_decay(
    results: list[dict[str, Any]],
    half_life_days: float = 30.0,
    date_field: str = "published",
) -> list[dict[str, Any]]:
    """Multiply each result's primary retrieval score by its recency decay.

    Results without a parseable date are left unchanged (decay factor 1.0).
    Mutates ``rrf_score`` (preferred) or ``similarity`` and tags each entry
    with ``recency_decay``. Returns results sorted by the decayed score,
    descending.

    Parameters
    ----------
    results:
        Fused retrieval results (RRF-fused or vector-ranked dicts).
    half_life_days:
        Decay half-life in days (0/negative disables the decay).
    date_field:
        Preferred metadata key for the document date (default ``published``).
    """
    if not results or half_life_days <= 0:
        return results
    now = datetime.now(UTC)
    for r in results:
        dt = extract_doc_date(r, date_field)
        if dt is None:
            r["recency_decay"] = 1.0
            continue
        age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
        decay = half_life_decay(age_days, half_life_days)
        r["recency_decay"] = round(decay, 6)
        if "rrf_score" in r:
            r["rrf_score"] = round(float(r.get("rrf_score", 0.0)) * decay, 6)
        elif "similarity" in r:
            r["similarity"] = round(float(r.get("similarity", 0.0)) * decay, 6)

    def _score(r: dict[str, Any]) -> float:
        return float(r.get("rrf_score", r.get("similarity", 0.0)) or 0.0)

    results.sort(key=_score, reverse=True)
    return results


def rrf_fuse_recency(
    ranked_lists: list[list[dict[str, Any]]],
    k: int = 60,
    entity_boost: float = 1.5,
    entity_doc_ids: set[str] | None = None,
    pillar_weights: list[float] | None = None,
    half_life_days: float = 30.0,
    date_field: str = "published",
) -> list[dict[str, Any]]:
    """Recency-aware reciprocal rank fusion.

    Same signature/behavior as ``rugbrain.rrf_fusion._rrf_fuse`` plus the
    half-life recency decay applied to the fused scores (see
    ``apply_recency_decay``). When ``half_life_days <= 0`` this is
    equivalent to plain RRF.
    """
    fused = _rrf_fuse(
        ranked_lists,
        k=k,
        entity_boost=entity_boost,
        entity_doc_ids=entity_doc_ids,
        pillar_weights=pillar_weights,
    )
    return apply_recency_decay(fused, half_life_days=half_life_days, date_field=date_field)
