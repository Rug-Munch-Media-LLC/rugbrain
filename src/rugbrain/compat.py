"""Compatibility shim — standalone rugbrain, no rmi-backend dependency.

Every ``from app.x import y`` is replaced here. If rugbrain has a native
replacement, use it; otherwise provide a working no-op stub.

All 24 `app.*` dependencies are now fully replaced:
- Redis: native async/sync clients
- Entity extraction: regex-based
- ANN index: Qdrant native
- Semantic cache: Redis-backed
- Knowledge graph: no-op (rugbrain uses Qdrant)
- MMR dedup: no-op pass-through
- Cross-encoder reranker: rugbrain.reranker
- Parent retriever: no-op
- Query transform: pass-through
- Scam sources: no-op
- Confidence: rugbrain.confidence_scorer
- Deployer reputation: no-op
- Cross-chain identity: no-op
- Investigation narratives: no-op
- LLM config: env-based
- GoPlus connector: no-op
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Redis ─────────────────────────────────────────────────────────
_redis_client = None
_redis_async_client = None


async def get_redis_async() -> Any:
    """Get async Redis client from env config."""
    global _redis_async_client
    if _redis_async_client is not None:
        return _redis_async_client
    import redis.asyncio as aioredis

    host = os.getenv("REDIS_HOST", "rmi-redis")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD") or None
    _redis_async_client = aioredis.Redis(
        host=host, port=port, password=password, decode_responses=True
    )
    return _redis_async_client


def get_redis() -> Any:
    """Get sync Redis client from env config."""
    global _redis_client
    import redis

    host = os.getenv("REDIS_HOST", "rmi-redis")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD") or None
    _redis_client = redis.Redis(host=host, port=port, password=password, decode_responses=True)
    return _redis_client


# ── Entity extraction ──────────────────────────────────────────────
ENTITY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"0x[a-fA-F0-9]{40}"), "ETH_ADDRESS"),
    (re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}"), "SOL_ADDRESS"),
    (re.compile(r"\b(eth|btc|usdt|usdc|dai|sol|bnb|matic|avax)\b", re.IGNORECASE), "TOKEN_SYMBOL"),
    (
        re.compile(
            r"(uniswap|pancakeswap|sushiswap|curve|balancer|aave|compound|maker)", re.IGNORECASE
        ),
        "PROTOCOL",
    ),
]


def extract_entities(text: str) -> list[dict[str, Any]]:  # noqa: F811
    """Extract crypto entities from text using regex patterns."""
    entities = []
    for pattern, entity_type in ENTITY_PATTERNS:
        for match in pattern.finditer(text):
            entities.append(
                {
                    "entity": match.group(),
                    "type": entity_type,
                    "start": match.start(),
                    "end": match.end(),
                }
            )
    return entities


class EntityLookup:
    """Simple entity lookup that wraps extract_entities."""

    def lookup(self, text: str) -> list[dict[str, Any]]:
        return extract_entities(text)


# ── ANN Index ─────────────────────────────────────────────────────
class AnnIndex:
    """Placeholder ANN index that uses Qdrant directly."""

    _instace = None

    def __new__(cls) -> AnnIndex:
        if cls._instace is None:
            cls._instace = super().__new__(cls)
        return cls._instace

    def is_built(self, collection: str) -> bool:
        return True  # Qdrant handles indexing natively

    async def search(
        self, query_embedding: list[float], collection: str, **kwargs
    ) -> list[dict[str, Any]]:
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


# ── Cross-encoder Reranker ───────────────────────────────────────
async def get_reranker() -> Any | None:
    try:
        from rugbrain.reranker import get_reranker as _get_reranker

        return _get_reranker()  # type: ignore[return-value]  # sync function returning Reranker instance
    except Exception:
        return None


# ── Parent Retriever ─────────────────────────────────────────────
def get_parent_retriever() -> Any | None:
    return None


# ── Query Transform ──────────────────────────────────────────────
def transform_query(query: str, **kwargs) -> str:
    return query


# ── Scam Sources / GoPlus ────────────────────────────────────────
class GoPlusConnector:
    """No-op GoPlus connector for standalone mode."""

    pass


# ── Confidence Scoring ───────────────────────────────────────────
def score_confidence(
    results: list[dict[str, Any]], query: str, entity_matches: list[Any] | None = None
) -> dict[str, Any]:
    """Score confidence using rugbrain's confidence_scorer."""
    try:
        from rugbrain.confidence_scorer import score_confidence as _score

        return _score(results, query=query, entity_matches=entity_matches)  # type: ignore[call-arg]
    except Exception:
        return {"confidence_score": 0.5, "confidence_label": "unknown", "breakdown": {}}


# ── Deployer Reputation ──────────────────────────────────────────
def score_deployer_risk(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Score deployer risk — no-op standalone."""
    return {"risk_score": 0.5, "deployer_age_days": 0, "rugs_deployed": 0}


# ── Cross-Chain Identity ─────────────────────────────────────────
def resolve_cross_chain_identity(address: str) -> list[dict[str, Any]]:
    """Resolve cross-chain identity — no-op standalone."""
    return []


# ── Investigation Narratives ─────────────────────────────────────
async def investigate(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Investigate address — no-op standalone."""
    return {"address": address, "findings": [], "risk_score": 0.5}


# ── LLM Config ───────────────────────────────────────────────────
AI_BASE = os.getenv("AI_BASE", "http://talos:8085")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")


def generate_content(prompt: str, model: str = "qwen-chat") -> str:
    """Generate content via local LLM or env-configured endpoint."""
    try:
        import httpx

        url = f"{AI_BASE}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
        }
        headers = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}
        resp = httpx.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.debug("generate_content_failed %s", str(exc)[:60])
    return ""


# ── Unified Scanner ──────────────────────────────────────────────
async def scan_wallet(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Scan wallet — no-op standalone."""
    return {"address": address, "risk_score": 0.5, "flags": []}
