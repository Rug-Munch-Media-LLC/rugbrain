"""Compatibility shim — replaces ``from app.x import y`` stubs with rugbrain-native implementations.

This module allows rugbrain to run standalone without rmi-backend. Every
``app.*`` import is intercepted here: if a rugbrain-native replacement exists,
use it; otherwise fall back to the import_guard stub.

Import pattern::

    # Instead of: from app.semantic_cache import get_semantic_cache
    from rugbrain.compat import get_semantic_cache
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Redis ─────────────────────────────────────────────────────────
_redis_client = None


async def get_redis_async() -> Any:
    """Get async Redis client from env config."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    import os

    import redis.asyncio as aioredis

    host = os.getenv("REDIS_HOST", "rmi-redis")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD") or None
    _redis_client = aioredis.Redis(host=host, port=port, password=password, decode_responses=True)
    return _redis_client


def get_redis() -> Any:
    """Get sync Redis client from env config."""
    global _redis_client
    import os

    import redis

    host = os.getenv("REDIS_HOST", "rmi-redis")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD") or None
    return redis.Redis(host=host, port=port, password=password, decode_responses=True)


# ── Entity extraction ─────────────────────────────────────────────
# Simple regex-based entity extraction for common crypto entities
import re

ENTITY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"0x[a-fA-F0-9]{40}"), "ETH_ADDRESS"),
    (re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}"), "SOL_ADDRESS"),
    (re.compile(r"\b(eth|btc|usdt|usdc|dai|sol|bnb|matic|avax)\b", re.IGNORECASE), "TOKEN_SYMBOL"),
    (re.compile(r"(uniswap|pancakeswap|sushiswap|curve|balancer|aave|compound|maker)", re.IGNORECASE), "PROTOCOL"),
]


def extract_entities(text: str) -> list[dict[str, Any]]:
    """Extract crypto entities from text using regex patterns."""
    entities = []
    for pattern, entity_type in ENTITY_PATTERNS:
        for match in pattern.finditer(text):
            entities.append({
                "entity": match.group(),
                "type": entity_type,
                "start": match.start(),
                "end": match.end(),
            })
    return entities


class EntityLookup:
    """Simple entity lookup that wraps extract_entities."""

    def lookup(self, text: str) -> list[dict[str, Any]]:
        return extract_entities(text)


# ── ANN Index (FAISS) ────────────────────────────────────────────
class AnnIndex:
    """Placeholder ANN index that uses Qdrant directly."""

    _instance = None

    def __new__(cls) -> AnnIndex:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def is_built(self, collection: str) -> bool:
        return True  # Qdrant handles indexing natively

    async def search(self, query_embedding: list[float], collection: str, **kwargs) -> list[dict[str, Any]]:
        return []


def get_ann_index() -> AnnIndex:
    return AnnIndex()


# ── Semantic Cache ───────────────────────────────────────────────
class SemanticCache:
    """No-op semantic cache — bypasses caching for standalone mode."""

    async def check(self, query_vec: list[float]) -> Any | None:
        return None

    async def store(self, query_vec: list[float], results: list[dict[str, Any]]) -> None:
        pass


def get_semantic_cache() -> SemanticCache:
    return SemanticCache()


# ── Knowledge Graph ──────────────────────────────────────────────
def get_knowledge_graph() -> Any | None:
    return None


# ── MMR Dedup ────────────────────────────────────────────────────
def mmr_dedup_results(results: list[dict[str, Any]], **kwargs) -> list[dict[str, Any]]:
    return results


# ── Cross-encoder Reranker (uses Infinity) ───────────────────────
async def get_reranker() -> Any | None:
    return None


# ── Parent Retriever ─────────────────────────────────────────────
def get_parent_retriever() -> Any | None:
    return None


# ── Query Transform ──────────────────────────────────────────────
def transform_query(query: str, **kwargs) -> str:
    return query


# ── Scam Sources ─────────────────────────────────────────────────
class GoPlusConnector:
    """No-op GoPlus connector for standalone mode."""
    pass
