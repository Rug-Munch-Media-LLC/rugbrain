"""B2 operations for RAG permanence (native b2sdk API).

Split out of :mod:`rugbrain.permanence` (2026-10-25, size ratchet).
Sync helpers run in the default executor; the async wrappers are the API.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from rugbrain.permanence_config import B2_APP_KEY, B2_BUCKET, B2_KEY_ID

logger = logging.getLogger(__name__)

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
