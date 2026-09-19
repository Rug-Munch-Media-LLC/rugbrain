"""Content-hash dedup via Redis.

``content_hash`` normalizes text and hashes it; ``is_duplicate`` /
``mark_ingested`` use the hash as a Redis key so re-ingest is idempotent.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any


def content_hash(text: str) -> str:
    """MD5 hash of normalized text for dedup."""
    # Normalize: lowercase, strip extra whitespace, remove punctuation noise
    normalized = re.sub(r"\s+", " ", text.lower().strip())
    normalized = re.sub(r"[^\w\s]", "", normalized)
    return hashlib.sha256(normalized.encode()).hexdigest()


async def is_duplicate(collection: str, text: str, redis_client: Any | None = None) -> bool:
    """Check if content already exists in the collection via hash dedup."""
    if redis_client is None:
        from rugbrain.search_service import _get_redis

        redis_client = await _get_redis()
    assert redis_client is not None, "redis_client required for dedup"

    h = content_hash(text)
    key = f"rag:dedup:{collection}:{h}"
    exists = await redis_client.exists(key)
    return bool(exists)


async def mark_ingested(
    collection: str,
    text: str,
    ttl: int = 604800,  # 7 days default
    redis_client: Any | None = None,
) -> None:
    """Mark content as ingested in dedup registry."""
    if redis_client is None:
        from rugbrain.search_service import _get_redis

        redis_client = await _get_redis()
    assert redis_client is not None, "redis_client required for mark_ingested"

    h = content_hash(text)
    key = f"rag:dedup:{collection}:{h}"
    await redis_client.setex(key, ttl, "1")
