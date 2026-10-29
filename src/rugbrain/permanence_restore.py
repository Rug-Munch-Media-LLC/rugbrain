"""Restore path: B2 permanence snapshots back into RAG collections.

Split out of :mod:`rugbrain.permanence` (2026-10-25, size ratchet).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from rugbrain.permanence_b2 import _b2_get

logger = logging.getLogger(__name__)


async def restore_all() -> dict[str, Any]:
    """Restore all RAG collections from latest B2 snapshots."""
    # Get collections and Redis connection
    try:
        from rugbrain.search_service import COLLECTIONS, _get_redis

        redis = await _get_redis()
    except Exception:
        return {"error": "Cannot connect to RAG service"}

    results: dict[str, Any] = {}

    for name in COLLECTIONS:
        try:
            # Get latest pointer
            latest_raw = await _b2_get(f"rag_snapshots/{name}/latest.json")
            if not latest_raw:
                results[name] = {"collection": name, "status": "no_snapshot"}
                continue

            latest = json.loads(latest_raw)
            key = latest["key"]

            # Download the snapshot
            data_raw = await _b2_get(key)
            if not data_raw:
                results[name] = {"collection": name, "status": "download_failed"}
                continue

            snapshot = json.loads(data_raw)
            items = snapshot.get("items", [])
            snapshot_ts = latest.get("timestamp", "")

            # Handle chunked snapshots - download all chunks
            total_chunks = latest.get("chunks", 1)
            if total_chunks > 1:
                for ci in range(1, total_chunks):
                    chunk_key = f"rag_snapshots/{name}/{snapshot_ts}_chunk{ci}.json"
                    chunk_raw = await _b2_get(chunk_key)
                    if chunk_raw:
                        chunk_data = json.loads(chunk_raw)
                        items.extend(chunk_data.get("items", []))

            # Restore to Redis: set each doc as string and add to index set
            restored = 0
            for item in items:
                try:
                    doc_id = item.get("id", "")
                    if not doc_id:
                        continue
                    # Store as JSON string (matches rag_service format)
                    await redis.set(f"rag:{name}:{doc_id}", json.dumps(item, default=str))
                    await redis.sadd(f"rag:idx:{name}", doc_id)
                    restored += 1
                except Exception:
                    logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

            results[name] = {
                "collection": name,
                "status": "restored",
                "count": restored,
                "total_in_snapshot": len(items),
                "snapshot_key": key,
            }
        except Exception as e:
            results[name] = {"collection": name, "error": str(e)[:200]}

    return results
