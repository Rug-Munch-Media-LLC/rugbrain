"""Incremental indexing for the RAG system.

Records document changes (adds, updates, deletes) in a Redis sorted set so
the system can avoid full rebuilds on every ingestion. A full rebuild is
only triggered when the delta queue exceeds a configurable threshold.
"""

from __future__ import annotations

import json
import logging
import time
from enum import StrEnum

log = logging.getLogger(__name__)

_DELTA_KEY = "rag:delta_queue"
_LAST_REBUILD_KEY = "rag:delta:last_rebuild"
_REBUILD_THRESHOLD = 1000


class DeltaAction(StrEnum):
    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"


def _redis():
    from app.core.redis import get_redis

    return get_redis()


class DeltaTracker:

    @staticmethod
    def _record(doc_id: str, collection: str, action: DeltaAction) -> None:
        r = _redis()
        if r is None:
            log.warning("delta_track_redis_unavailable")
            return
        entry = json.dumps({
            "doc_id": doc_id,
            "collection": collection,
            "action": action,
        })
        r.zadd(_DELTA_KEY, {entry: time.time()})
        log.debug("delta_track action=%s doc_id=%s collection=%s", action, doc_id, collection)

    @staticmethod
    def track_add(doc_id: str, collection: str) -> None:
        DeltaTracker._record(doc_id, collection, DeltaAction.ADD)

    @staticmethod
    def track_update(doc_id: str, collection: str) -> None:
        DeltaTracker._record(doc_id, collection, DeltaAction.UPDATE)

    @staticmethod
    def track_delete(doc_id: str, collection: str) -> None:
        DeltaTracker._record(doc_id, collection, DeltaAction.DELETE)

    @staticmethod
    def get_deltas(since_timestamp: float | None = None) -> list[tuple[str, str, str]]:
        """Return list of (doc_id, collection, action) since the given timestamp.

        If since_timestamp is None, returns all deltas.
        """
        r = _redis()
        if r is None:
            return []
        if since_timestamp is not None:
            raw = r.zrangebyscore(_DELTA_KEY, since_timestamp, "+inf")
        else:
            raw = r.zrange(_DELTA_KEY, 0, -1)
        results: list[tuple[str, str, str]] = []
        for entry in raw:
            try:
                e = json.loads(entry)
                results.append((e["doc_id"], e["collection"], e["action"]))
            except (json.JSONDecodeError, KeyError):
                log.debug("delta_parse_error entry=%s", entry)
        return results

    @staticmethod
    def get_queue_size() -> int:
        r = _redis()
        if r is None:
            return 0
        return r.zcard(_DELTA_KEY) or 0

    @staticmethod
    def clear_deltas() -> None:
        r = _redis()
        if r is None:
            return
        r.delete(_DELTA_KEY)
        r.set(_LAST_REBUILD_KEY, time.time())
        log.info("delta_queue_cleared")

    @staticmethod
    def get_last_rebuild_time() -> float | None:
        r = _redis()
        if r is None:
            return None
        val = r.get(_LAST_REBUILD_KEY)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
        return None


def rebuild_if_needed(threshold: int = _REBUILD_THRESHOLD) -> bool:
    """Check the delta queue size and trigger a full rebuild if it exceeds threshold.

    Returns True if a rebuild was triggered, False otherwise.
    """
    tracker = DeltaTracker()
    size = tracker.get_queue_size()
    if size >= threshold:
        log.info("rebuild_triggered delta_count=%d threshold=%d", size, threshold)
        tracker.clear_deltas()
        return True

    log.debug("rebuild_skipped delta_count=%d threshold=%d", size, threshold)
    return False
