"""Query criteria for crypto-code retrieval: endpoint and keyword ranking.

Split from `retriever.py` on 2026-09-29. One job: resolve the Qdrant endpoint,
turn facets into a filter, and rank candidate points by keyword overlap. The
retriever itself orchestrates; it does not decide what to match on.
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.crypto_code_pkg.attribution import keyword_score

logger = logging.getLogger(__name__)

#: Payload fields a facet may filter on.
FILTER_FIELDS = ("verdict", "library_id", "category", "severity", "ecosystem")


async def _resolve_base_url() -> str:
    """Async resolve the Qdrant base URL via the shared fallback chain."""
    from rugbrain.qdrant_store import _resolve_qdrant_url

    return await _resolve_qdrant_url()


def build_qdrant_filter(**facets: str | None) -> dict[str, Any] | None:
    """Build a match-any Qdrant filter from the non-empty *facets*."""
    match_fields = {f: v for f, v in facets.items() if v}
    return {"match": match_fields} if match_fields else None


def _rank_points(query: str, points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score points by keyword overlap, best first."""
    return sorted(
        (
            {
                "id": p.get("id"),
                "keyword_score": keyword_score(query, p.get("payload") or {}),
            }
            for p in points
        ),
        key=lambda x: x["keyword_score"],
        reverse=True,
    )
