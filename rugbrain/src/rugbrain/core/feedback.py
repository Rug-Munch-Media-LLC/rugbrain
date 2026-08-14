"""RAG Feedback Loop — track which RAG results were useful.

This is the USER-FACING feedback loop (implicit from clicks, explicit from
ratings).  It complements the SCANNER-TO-RAG weight adjustment in
``app.rag_feedback`` (which boosts/penalizes documents based on scanner
verdicts).

Design:
    Every ``RAGIntelligenceEnricher`` call creates a ``query_id``.
    Downstream code (UI, API handlers) calls ``record_feedback()`` with
    that ID to signal usefulness.  The feedback is stored in Redis with
    a TTL and aggregated periodically for quality dashboards.

Redis key schema:
    rag:feedback:{query_id}  → JSON {query_id, was_helpful, timestamp, ...}
    rag:feedback:counts      → HASH {helpful: N, unhelpful: N, total: N}
"""

from __future__ import annotations

import contextlib
import json
import time
from datetime import UTC, datetime
from typing import Any, cast

from rugbrain._logging import get_logger

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

FEEDBACK_TTL = 86400 * 30  # 30 days
FEEDBACK_COUNTS_KEY = "rag:feedback:counts"
FEEDBACK_PREFIX = "rag:feedback:"

# ── Public API ────────────────────────────────────────────────────────────


async def record_feedback(
    query_id: str,
    was_helpful: bool,
    source: str = "unknown",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record whether a RAG query result was helpful.

    Args:
        query_id: Opaque ID returned by ``RAGIntelligenceEnricher`` methods
            (e.g., ``enrich_scan_result().query_id``).
        was_helpful: ``True`` if the user found the RAG result useful,
            ``False`` if not.
        source: Where the feedback came from — ``"explicit_rating"``,
            ``"implicit_click"``, ``"api"``, etc.
        metadata: Optional additional context (e.g., ``{"token": "0x..."}``).

    Returns:
        Dict with ``status``, ``query_id``, and ``recorded`` boolean.
    """
    if not query_id or not isinstance(query_id, str):
        logger.warning("feedback_invalid_query_id", query_id=query_id)
        return {"status": "error", "error": "invalid query_id", "recorded": False}

    record = {
        "query_id": query_id,
        "was_helpful": bool(was_helpful),
        "source": source,
        "timestamp": datetime.now(UTC).isoformat(),
        "ts": time.time(),
        "metadata": metadata or {},
    }

    try:
        from rugbrain.storage.cache import get_cache

        r = get_cache()
        if r is None:
            logger.warning("feedback_redis_unavailable")
            return {"status": "degraded", "query_id": query_id, "recorded": False}

        key = f"{FEEDBACK_PREFIX}{query_id}"

        # Store the record with TTL
        r.setex(key, FEEDBACK_TTL, json.dumps(record))

        # Update aggregate counters
        r.hincrby(FEEDBACK_COUNTS_KEY, "total", 1)
        field = "helpful" if was_helpful else "unhelpful"
        r.hincrby(FEEDBACK_COUNTS_KEY, field, 1)

        logger.info(
            "feedback_recorded",
            query_id=query_id[:24],
            was_helpful=was_helpful,
            source=source,
        )
        return {"status": "ok", "query_id": query_id, "recorded": True}

    except (ConnectionError, OSError, TimeoutError) as exc:
        logger.warning("feedback_store_failed", error=str(exc)[:80])
        return {"status": "error", "error": str(exc), "recorded": False}


async def get_feedback(query_id: str) -> dict[str, Any] | None:
    """Retrieve a single feedback record by query_id.

    Returns the parsed record dict, or ``None`` if not found / expired.
    """
    try:
        from rugbrain.storage.cache import get_cache

        r = get_cache()
        if r is None:
            return None

        raw = r.get(f"{FEEDBACK_PREFIX}{query_id}")
        if raw:
            return cast(dict[str, Any], json.loads(raw))
    except (ConnectionError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        logger.debug("feedback_get_failed", error=str(exc)[:60])
    return None


async def get_feedback_counts() -> dict[str, int | float]:
    """Get aggregate feedback counts.

    Returns:
        ``{"total": N, "helpful": N, "unhelpful": N, "helpfulness_ratio": float}``
    """
    try:
        from rugbrain.storage.cache import get_cache

        r = get_cache()
        if r is None:
            return {"total": 0, "helpful": 0, "unhelpful": 0, "helpfulness_ratio": 0.0}

        raw = r.hgetall(FEEDBACK_COUNTS_KEY)
        total = int(raw.get(b"total", raw.get("total", 0)))
        helpful = int(raw.get(b"helpful", raw.get("helpful", 0)))
        unhelpful = int(raw.get(b"unhelpful", raw.get("unhelpful", 0)))
        ratio = round(helpful / max(total, 1), 4)

        return {
            "total": total,
            "helpful": helpful,
            "unhelpful": unhelpful,
            "helpfulness_ratio": ratio,
        }
    except (ConnectionError, OSError, TimeoutError) as exc:
        logger.debug("feedback_counts_failed", error=str(exc)[:60])
        return {"total": 0, "helpful": 0, "unhelpful": 0, "helpfulness_ratio": 0.0}


async def get_recent_feedback(limit: int = 20) -> list[dict[str, Any]]:
    """Get the most recent feedback records (best-effort, no ordering guarantee).

    Scans Redis for ``rag:feedback:*`` keys and returns the parsed records.
    This is O(N) on the number of keys — use sparingly (dashboards only).
    """
    try:
        from rugbrain.storage.cache import get_cache

        r = get_cache()
        if r is None:
            return []

        cursor = 0
        records: list[dict[str, Any]] = []
        while True:
            cursor, keys = r.scan(cursor, match=f"{FEEDBACK_PREFIX}*", count=50)
            for key in keys:
                raw = r.get(key)
                if raw:
                    with contextlib.suppress(json.JSONDecodeError):
                        records.append(cast(dict[str, Any], json.loads(raw)))
            if cursor == 0:
                break

        # Sort by timestamp descending, return most recent
        records.sort(key=lambda x: x.get("ts", 0), reverse=True)
        return records[:limit]

    except (ConnectionError, OSError, TimeoutError) as exc:
        logger.debug("recent_feedback_failed", error=str(exc)[:60])
        return []


# ── Implicit feedback helpers ─────────────────────────────────────────────


async def record_implicit_helpful(query_id: str) -> dict[str, Any]:
    """Record that a user implicitly found a RAG result helpful.

    Call this when a user clicks through, copies, or spends significant
    time on a RAG-enriched result.
    """
    return await record_feedback(
        query_id=query_id,
        was_helpful=True,
        source="implicit_click",
        metadata={"implicit": True},
    )


async def record_implicit_unhelpful(query_id: str) -> dict[str, Any]:
    """Record that a user implicitly dismissed a RAG result.

    Call this when a user closes/clicks-away from a RAG-enriched result
    without engaging.
    """
    return await record_feedback(
        query_id=query_id,
        was_helpful=False,
        source="implicit_dismiss",
        metadata={"implicit": True},
    )


__all__ = [
    "get_feedback",
    "get_feedback_counts",
    "get_recent_feedback",
    "record_feedback",
    "record_implicit_helpful",
    "record_implicit_unhelpful",
]
