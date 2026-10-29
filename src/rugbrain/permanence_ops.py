"""Permanence maintenance ops: nightly cycle + B2/cache statistics.

Split out of :mod:`rugbrain.permanence` (2026-10-25, size ratchet).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from rugbrain.permanence_b2 import _b2_list
from rugbrain.permanence_config import (
    B2_APP_KEY,
    B2_BUCKET,
    B2_KEY_ID,
    EMBEDDING_VERSION,
    LOCAL_CACHE,
    LOCAL_RETENTION_DAYS,
)
from rugbrain.permanence_snapshot import snapshot_all

logger = logging.getLogger(__name__)


async def nightly_cycle() -> dict[str, Any]:
    """Full nightly cycle: snapshot to B2, clean local cache."""
    snap = await snapshot_all()
    ok = sum(1 for v in snap.values() if v.get("status") in ("uploaded", "empty"))
    total = len(snap)

    # Clean local cache older than retention period
    cleaned = 0
    cutoff = datetime.now(UTC) - timedelta(days=LOCAL_RETENTION_DAYS)
    local_files = await asyncio.get_running_loop().run_in_executor(
        None, lambda: list(LOCAL_CACHE.glob("*.json"))
    )
    for f in local_files:
        try:
            stat = await asyncio.get_running_loop().run_in_executor(None, f.stat)
            if datetime.fromtimestamp(stat.st_mtime, tz=UTC) < cutoff:
                await asyncio.get_running_loop().run_in_executor(None, f.unlink)
                cleaned += 1
        except Exception:
            logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    return {
        "snapshot_results": {k: v.get("status", "error") for k, v in snap.items()},
        "collections_ok": ok,
        "collections_total": total,
        "local_cache_cleaned": cleaned,
    }


async def b2_stats() -> dict[str, Any]:
    """Get B2 usage + local cache stats."""
    # Get collections and Redis connection
    redis: Any = None
    try:
        from rugbrain.search_service import COLLECTIONS, _get_redis

        redis = await _get_redis()
        has_redis = True
    except Exception:
        COLLECTIONS = []  # noqa: N806 - fallback assignment, uppercase intentional for visibility
        has_redis = False

    # B2 bucket stats
    objects = await _b2_list("rag_snapshots/")
    total_size = 0
    snapshot_count = 0
    for obj in objects:
        size = int(obj.get("size", 0))
        total_size += size
        snapshot_count += 1

    # Local cache stats
    local_files = await asyncio.get_running_loop().run_in_executor(
        None, lambda: list(LOCAL_CACHE.glob("*.json"))
    )
    local_size = 0
    for f in local_files:
        stat = await asyncio.get_running_loop().run_in_executor(None, f.stat)
        local_size += stat.st_size

    # Collection stats (from Redis)
    coll_info = {}
    if has_redis:
        for name in COLLECTIONS:
            try:
                count = await redis.scard(f"rag:idx:{name}")
                coll_info[name] = count
            except Exception:
                coll_info[name] = 0

    return {
        "b2": {
            "bucket": B2_BUCKET,
            "snapshot_count": snapshot_count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "has_credentials": bool(B2_KEY_ID and B2_APP_KEY),
        },
        "local": {
            "path": str(LOCAL_CACHE),
            "file_count": len(local_files),
            "size_bytes": local_size,
            "retention_days": LOCAL_RETENTION_DAYS,
        },
        "collections": coll_info,
        "model_version": EMBEDDING_VERSION,
    }
