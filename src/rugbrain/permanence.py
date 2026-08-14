"""
RAG Permanence v2 - Backblaze B2 cold storage (native B2 API via b2sdk).
Hot (Redis) → Warm (local 7-day cache) → Cold (B2 permanent + HF mirror).

Migrated from Cloudflare R2 to Backblaze B2 on 2026-07-31.
Uses B2_APPLICATION_KEY_ID / B2_APPLICATION_KEY credentials (b2sdk).
HF mirror: opt-in dual-write to HuggingFace Storage Buckets via hf CLI.
"""

import asyncio
import contextlib
import io
import json
import logging
import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════
B2_KEY_ID = os.getenv("B2_APPLICATION_KEY_ID", "")
B2_APP_KEY = os.getenv("B2_APPLICATION_KEY", "")
B2_BUCKET = os.getenv("B2_BUCKET_NAME", "rmi-rag-permanence")

RAG_HF_MIRROR = os.getenv("RAG_HF_MIRROR", "false").lower() in ("true", "1", "yes")
HF_REPO = os.getenv("RAG_HF_REPO", "your-username/your-repo")
HF_TOKEN = os.getenv("HF_TOKEN", "")

LOCAL_CACHE = Path(os.getenv("RAG_LOCAL_CACHE", "/data/rag-storage"))
LOCAL_RETENTION_DAYS = 7
EMBEDDING_VERSION = os.getenv("EMBEDDING_MODEL_VERSION", "v1")

LOCAL_CACHE.mkdir(parents=True, exist_ok=True)

# Lazy b2sdk client (authorized on first use)
_b2_api = None


def _get_b2_api() -> Any:
    """Authorize a b2sdk client from B2_APPLICATION_KEY_ID/B2_APPLICATION_KEY."""
    global _b2_api
    if _b2_api is None:
        if not B2_KEY_ID or not B2_APP_KEY:
            raise RuntimeError(
                "B2 credentials missing — set B2_APPLICATION_KEY_ID and B2_APPLICATION_KEY"
            )
        from b2sdk.v2 import B2Api, InMemoryAccountInfo

        api = B2Api(InMemoryAccountInfo())
        api.authorize_account("production", B2_KEY_ID, B2_APP_KEY)
        _b2_api = api
    return _b2_api


def _b2_bucket() -> Any:
    """Return the B2 bucket handle for *B2_BUCKET* (sync helper)."""
    return _get_b2_api().get_bucket_by_name(B2_BUCKET)


# ═══════════════════════════════════════════════════════════════
# B2 OPERATIONS (native B2 API via b2sdk; sync helpers run in executor)
# ═══════════════════════════════════════════════════════════════
def _b2_put_sync(key: str, data: bytes, content_type: str) -> bool:
    """Upload an object to B2."""
    try:
        bucket = _b2_bucket()
        bucket.upload_bytes(data_bytes=data, file_name=key, content_type=content_type)
        return True
    except Exception as e:
        logger.error("B2 PUT %s: %s", key, e)
        return False


async def _b2_put(key: str, data: bytes, content_type: str = "application/json") -> bool:
    """Upload an object to B2."""
    if not B2_KEY_ID or not B2_APP_KEY:
        logger.error("B2 credentials not set (B2_APPLICATION_KEY_ID / B2_APPLICATION_KEY)")
        return False
    return await asyncio.get_running_loop().run_in_executor(
        None, _b2_put_sync, key, data, content_type
    )


def _b2_get_sync(key: str) -> bytes | None:
    """Download an object from B2, returning bytes or None."""
    try:
        bucket = _b2_bucket()
        buf = io.BytesIO()
        bucket.download_file_by_name(key).save(buf)
        return buf.getvalue()
    except Exception as e:
        logger.error("B2 GET %s: %s", key, e)
        return None


async def _b2_get(key: str) -> bytes | None:
    """Download an object from B2."""
    if not B2_KEY_ID or not B2_APP_KEY:
        return None
    return await asyncio.get_running_loop().run_in_executor(None, _b2_get_sync, key)


def _b2_list_sync(prefix: str = "") -> list[dict[str, Any]]:
    """List objects in the B2 bucket under *prefix*."""
    try:
        bucket = _b2_bucket()
        out: list[dict[str, Any]] = []
        for version, _folder in bucket.ls(prefix, recursive=True):
            out.append({"file_name": version.file_name, "size": version.size})
        return out
    except Exception as e:
        logger.error("B2 LIST: %s", e)
        return []


async def _b2_list(prefix: str = "") -> list[dict[str, Any]]:
    """List objects in the B2 bucket."""
    if not B2_KEY_ID or not B2_APP_KEY:
        return []
    return await asyncio.get_running_loop().run_in_executor(None, _b2_list_sync, prefix)


def _b2_delete_sync(key: str) -> bool:
    """Delete an object from B2."""
    try:
        bucket = _b2_bucket()
        for version, _folder in bucket.ls(key, recursive=True):
            if version.file_name == key:
                bucket.delete_file_version(version.id_, version.file_name)
                return True
        return True  # already absent
    except Exception as e:
        logger.error("B2 DELETE %s: %s", key, e)
        return False


async def _b2_delete(key: str) -> bool:
    """Delete an object from B2."""
    if not B2_KEY_ID or not B2_APP_KEY:
        return False
    return await asyncio.get_running_loop().run_in_executor(None, _b2_delete_sync, key)


# ═══════════════════════════════════════════════════════════════
# HUGGINGFACE STORAGE BUCKETS MIRROR
# ═══════════════════════════════════════════════════════════════
def _hf_put(key: str, data: bytes) -> bool:
    """Upload an object to HuggingFace Storage Buckets via hf CLI.

    Writes data to a temp file and calls `hf storage buckets upload`.
    Uses HF_TOKEN env var for authentication.
    """
    hf_token = HF_TOKEN if HF_TOKEN else os.getenv("HF_TOKEN", "")

    if not hf_token:
        logger.error("HF_TOKEN not set")
        return False

    hf_key = f"rag/{key}"

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        try:
            tmp.write(data)
            tmp.flush()
            result = subprocess.run(  # noqa: S603
                [  # noqa: S607
                    "hf",
                    "storage",
                    "buckets",
                    "upload",
                    "--repo",
                    HF_REPO,
                    "--key",
                    hf_key,
                    "--token",
                    hf_token,
                    tmp.name,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                return True
            logger.error("HF PUT %s: %s %s", key, result.returncode, result.stderr[:200])
            return False
        except Exception as e:
            logger.error("HF PUT %s: %s", key, e)
            return False
        finally:
            with contextlib.suppress(OSError):
                Path(tmp.name).unlink()


# ═══════════════════════════════════════════════════════════════
# RAG PERSISTENCE OPERATIONS
# ═══════════════════════════════════════════════════════════════
async def snapshot_all() -> dict[str, Any]:
    """Snapshot all RAG collections to B2. Returns per-collection status."""
    results: dict[str, Any] = {}
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    # Get collections from rag_service (Redis-backed)
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
