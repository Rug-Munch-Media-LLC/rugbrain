"""Snapshot path: RAG collections to B2 (permanence).

Split out of :mod:`rugbrain.permanence` (2026-10-25, size ratchet).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from rugbrain.permanence_b2 import _b2_put
from rugbrain.permanence_config import (
    EMBEDDING_VERSION,
    LOCAL_CACHE,
    RAG_HF_MIRROR,
)
from rugbrain.permanence_hf import _hf_put

logger = logging.getLogger(__name__)


async def snapshot_all() -> dict[str, Any]:
    """Snapshot all RAG collections to B2. Returns per-collection status."""
    results: dict[str, Any] = {}
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    # Get collections from rag_service (Redis-backed)
    redis: Any = None
    try:
        from rugbrain.search_service import COLLECTIONS, _get_redis

        redis = await _get_redis()
        has_redis = True
    except Exception:
        COLLECTIONS = []  # noqa: N806 - fallback assignment, uppercase intentional for visibility
        has_redis = False

    for name in COLLECTIONS:
        try:
            # Get document IDs from Redis
            if not has_redis:
                results[name] = {"collection": name, "status": "skipped", "count": 0}
                continue

            doc_ids = await redis.smembers(f"rag:idx:{name}")
            count = len(doc_ids) if doc_ids else 0
            if count == 0:
                results[name] = {"collection": name, "status": "empty", "count": 0}
                continue

            # Collect all documents for this collection
            # Redis stores docs as strings at rag:{collection}:{id}, NOT as hashes
            items = []
            for doc_id in doc_ids:
                try:
                    raw = await redis.get(f"rag:{name}:{doc_id}")
                    if raw:
                        doc = json.loads(raw) if isinstance(raw, str) else raw
                        items.append(doc)
                except Exception:
                    logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

            # Upload in chunks if payload is large (B2 large-file threshold but we chunk at 5MB)
            MAX_CHUNK_SIZE = 5 * 1024 * 1024  # 5MB per chunk  # noqa: N806 - byte size constant
            all_items = items
            chunk_idx = 0
            total_uploaded = 0

            while all_items:
                # Build chunk - add items until we exceed size limit
                chunk_items: list[Any] = []
                chunk_size = 0
                while all_items and chunk_size < MAX_CHUNK_SIZE:
                    item = all_items.pop(0)
                    item_json = json.dumps(item, default=str)
                    if chunk_size + len(item_json.encode()) > MAX_CHUNK_SIZE and chunk_items:
                        # Put it back and upload this chunk
                        all_items.insert(0, item)
                        break
                    chunk_items.append(item)
                    chunk_size += len(item_json.encode())

                payload = json.dumps(
                    {
                        "collection": name,
                        "version": EMBEDDING_VERSION,
                        "timestamp": ts,
                        "chunk": chunk_idx,
                        "count": len(chunk_items),
                        "items": chunk_items,
                    },
                    default=str,
                ).encode()

                key = f"rag_snapshots/{name}/{ts}_chunk{chunk_idx}.json"
                ok = await _b2_put(key, payload)
                total_uploaded += len(chunk_items)

                if RAG_HF_MIRROR:
                    try:
                        hf_ok = await asyncio.get_running_loop().run_in_executor(
                            None,
                            _hf_put,
                            key,
                            payload,
                        )
                        if not hf_ok:
                            logger.warning("HF mirror failed for chunk: %s", key)
                    except Exception as e:
                        logger.warning("HF mirror exception for %s: %s", key, e)

                if not ok and chunk_idx == 0:
                    # First chunk failed - report failure
                    results[name] = {
                        "collection": name,
                        "status": "failed",
                        "count": count,
                        "key": key,
                        "size_bytes": len(payload),
                        "chunks_uploaded": chunk_idx,
                    }
                    break
                chunk_idx += 1

            # Save latest pointer (only if at least one chunk succeeded)
            if chunk_idx > 0 or count == 0:
                await _b2_put(
                    f"rag_snapshots/{name}/latest.json",
                    json.dumps(
                        {
                            "key": f"rag_snapshots/{name}/{ts}",
                            "timestamp": ts,
                            "count": count,
                            "version": EMBEDDING_VERSION,
                            "chunks": chunk_idx,
                        }
                    ).encode(),
                )

                if RAG_HF_MIRROR:
                    try:
                        latest_payload = json.dumps(
                            {
                                "key": f"rag_snapshots/{name}/{ts}",
                                "timestamp": ts,
                                "count": count,
                                "version": EMBEDDING_VERSION,
                                "chunks": chunk_idx,
                            }
                        ).encode()
                        await asyncio.get_running_loop().run_in_executor(
                            None,
                            _hf_put,
                            f"rag_snapshots/{name}/latest.json",
                            latest_payload,
                        )
                    except Exception as e:
                        logger.warning("HF mirror failed for latest.json of %s: %s", name, e)

                results[name] = {
                    "collection": name,
                    "status": "uploaded" if count > 0 else "empty",
                    "count": count,
                    "key": f"rag_snapshots/{name}/{ts}",
                    "size_bytes": sum(len(json.dumps(i, default=str).encode()) for i in items),
                    "chunks": chunk_idx,
                }
        except Exception as e:
            results[name] = {"collection": name, "error": str(e)[:200]}

    # Save local metadata
    meta = {
        "last_snapshot": ts,
        "results": {k: v.get("status", "error") for k, v in results.items()},
    }
    (LOCAL_CACHE / "snapshot_meta.json").write_text(json.dumps(meta, indent=2))

    return results
