"""
RAG Ingestion & Retrieval Service v4
====================================
Facade — re-exports from the ``search`` package for backward compatibility.

New code should import directly from ``search.engine``, ``search.filters``,
``search.ranking``, or ``search`` package.
"""

import logging
import os

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Shared state — defined here BEFORE the search package import
# so that sibling modules (sparse_search, scam_detection, etc.)
# can access them without circular-import deadlock.
# ═══════════════════════════════════════════════════════════════

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

_redis_pool = None


async def _get_redis():
    """Get or create a shared Redis connection (singleton pattern to prevent leaks)."""
    global _redis_pool
    import redis.asyncio as redis

    if _redis_pool is not None:
        try:
            await _redis_pool.ping()
        except Exception:
            _redis_pool = None
    if _redis_pool is None:
        _redis_pool = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD or None,
            db=0,
            decode_responses=True,
        )
    return _redis_pool


_seeded = False
_pattern_cache: dict = {}
_pattern_cache_loaded = False

# ═══════════════════════════════════════════════════════════════
# Search package import — safe now that shared state is defined
# ═══════════════════════════════════════════════════════════════

from rmi_rag.search import *  # noqa: E402, F403
