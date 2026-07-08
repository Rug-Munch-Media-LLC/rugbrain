"""
RAG Permanence v2 - Cloudflare R2 cold storage via REST API.
Hot (Redis) → Warm (local 7-day cache) → Cold (R2 permanent).

R2 free tier: 10GB storage, 10M Class A ops/month, zero egress.
Uses CF REST API with Bearer token - no S3 credentials needed.
"""

import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "8f9bd9165c1250b426c66dc1967deefd")
R2_BUCKET = os.getenv("R2_BUCKET", "rag-backup")
R2_API_TOKEN = os.getenv("R2_API_TOKEN", "")
R2_BASE = f"https://api.cloudflare.com/client/v4/accounts/{R2_ACCOUNT_ID}/r2/buckets/{R2_BUCKET}"

LOCAL_CACHE = Path(os.getenv("RAG_LOCAL_CACHE", "/data/rag-storage"))
LOCAL_RETENTION_DAYS = 7
EMBEDDING_VERSION = os.getenv("EMBEDDING_MODEL_VERSION", "v1")

LOCAL_CACHE.mkdir(parents=True, exist_ok=True)


def _headers():
    return {"Authorization": f"Bearer {R2_API_TOKEN}", "Content-Type": "application/json"}


# ═══════════════════════════════════════════════════════════════
# R2 REST API OPERATIONS
# ═══════════════════════════════════════════════════════════════
async def _r2_put(key: str, data: bytes, content_type: str = "application/json") -> bool:
    """Upload an object to R2 via REST API."""
    if not R2_API_TOKEN:
        logger.error("R2_API_TOKEN not set")
        return False
    url = f"{R2_BASE}/objects/{key}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.put(
                url,
                content=data,
                headers={"Authorization": f"Bearer {R2_API_TOKEN}", "Content-Type": content_type},
            )
            if resp.status_code == 200:
                return True
            logger.error("R2 PUT %s: %s %s", key, resp.status_code, resp.text[:200])
            return False
        except Exception as e:
            logger.error("R2 PUT %s: %s", key, e)
            return False


async def _r2_get(key: str) -> bytes | None:
    """Download an object from R2 via REST API."""
    if not R2_API_TOKEN:
        return None
    url = f"{R2_BASE}/objects/{key}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, headers=_headers())
            if resp.status_code == 200:
                return resp.content
            return None
        except Exception as e:
            logger.error("R2 GET %s: %s", key, e)
            return None


async def _r2_list(prefix: str = "") -> list:
    """List objects in R2 bucket."""
    if not R2_API_TOKEN:
        return []
    url = f"{R2_BASE}/objects"
    if prefix:
        url += f"?prefix={prefix}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, headers=_headers())
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result", [])
            return []
        except Exception as e:
            logger.error("R2 LIST: %s", e)
            return []


async def _r2_delete(key: str) -> bool:
    """Delete an object from R2."""
    if not R2_API_TOKEN:
        return False
    url = f"{R2_BASE}/objects/{key}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.delete(url, headers=_headers())
            return resp.status_code in (200, 204, 404)
        except Exception as e:
            logger.error("R2 DELETE %s: %s", key, e)
            return False


# ═══════════════════════════════════════════════════════════════
# RAG PERSISTENCE OPERATIONS
# ═══════════════════════════════════════════════════════════════
async def snapshot_all():
    """Snapshot all RAG collections to R2. Returns per-collection status."""
    results = {}
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    # Get collections from rag_service (Redis-backed)
    try:
        from rmi_rag.search_service import COLLECTIONS, _get_redis

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

            # Upload in chunks if payload is large (R2 REST limit ~5GB but we chunk at 5MB)
            MAX_CHUNK_SIZE = 5 * 1024 * 1024  # 5MB per chunk  # noqa: N806 - byte size constant
            all_items = items
            chunk_idx = 0
            total_uploaded = 0

            while all_items:
                # Build chunk - add items until we exceed size limit
                chunk_items = []
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
                ok = await _r2_put(key, payload)
                total_uploaded += len(chunk_items)

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
                await _r2_put(
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


async def restore_all():
    """Restore all RAG collections from latest R2 snapshots."""
    # Get collections and Redis connection
    try:
        from rmi_rag.search_service import COLLECTIONS, _get_redis

        redis = await _get_redis()
    except Exception:
        return {"error": "Cannot connect to RAG service"}

    results = {}

    for name in COLLECTIONS:
        try:
            # Get latest pointer
            latest_raw = await _r2_get(f"rag_snapshots/{name}/latest.json")
            if not latest_raw:
                results[name] = {"collection": name, "status": "no_snapshot"}
                continue

            latest = json.loads(latest_raw)
            key = latest["key"]

            # Download the snapshot
            data_raw = await _r2_get(key)
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
                    chunk_raw = await _r2_get(chunk_key)
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


async def nightly_cycle():
    """Full nightly cycle: snapshot to R2, clean local cache."""
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


async def r2_stats():
    """Get R2 usage + local cache stats."""
    # Get collections and Redis connection
    try:
        from rmi_rag.search_service import COLLECTIONS, _get_redis

        redis = await _get_redis()
        has_redis = True
    except Exception:
        COLLECTIONS = []  # noqa: N806 - fallback assignment, uppercase intentional for visibility
        has_redis = False

    # R2 bucket stats
    objects = await _r2_list("rag_snapshots/")
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
    local_size = (
        sum(
            (await asyncio.get_running_loop().run_in_executor(None, f.stat)).st_size
            for f in local_files
        )
        if local_files
        else 0
    )

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
        "r2": {
            "bucket": R2_BUCKET,
            "endpoint": R2_BASE,
            "snapshot_count": snapshot_count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "has_token": bool(R2_API_TOKEN),
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

