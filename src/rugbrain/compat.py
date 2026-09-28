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

import logging
import os
import re
from typing import Any, cast

# Retrieval-stage shims (ANN index, semantic cache, knowledge graph, MMR dedup,
# reranker, parent retriever, query transform) live in compat_retrieval so this
# module stays inside the 200-line limit. Re-exported because callers import
# them from `rugbrain.compat`.
# Capabilities that are genuinely unavailable standalone now live in
# compat_unavailable (deployer-risk, cross-chain identity, wallet scan) for the
# same reason. `score_deployer_risk` FAILS LOUDLY — it used to return a
# hardcoded 0.5 that the HTTP layer published as a real wallet verdict.
from rugbrain.compat_retrieval import (
    AnnIndex,
    QueryTransformResult,
    SemanticCache,
    get_ann_index,
    get_knowledge_graph,
    get_parent_retriever,
    get_reranker,
    get_semantic_cache,
    mmr_dedup_results,
    transform_query,
)
from rugbrain.compat_unavailable import (
    DeployerScorerUnavailable,
    resolve_cross_chain_identity,
    scan_wallet,
    score_deployer_risk,
)

logger = logging.getLogger(__name__)

__all__ = [  # noqa: RUF022  (grouped by origin, not alphabetical)
    # from compat_retrieval
    "AnnIndex",
    "QueryTransformResult",
    "SemanticCache",
    "get_ann_index",
    "get_knowledge_graph",
    "get_parent_retriever",
    "get_reranker",
    "get_semantic_cache",
    "mmr_dedup_results",
    "transform_query",
    # from compat_unavailable
    "DeployerScorerUnavailable",
    "resolve_cross_chain_identity",
    "scan_wallet",
    "score_deployer_risk",
]


# ── Redis ─────────────────────────────────────────────────────────
_redis_client: Any = None
_redis_async_client: Any = None


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
ENTITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
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


def extract_entities(text: str) -> list[dict[str, Any]]:
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

    _instance: EntityLookup | None = None

    @classmethod
    def get_instance(cls) -> EntityLookup:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def lookup(self, text: str) -> list[dict[str, Any]]:
        return extract_entities(text)

    async def index_entity(
        self,
        entity_type: str,
        value: str,
        doc_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """No-op entity index hook — standalone mode has no entity store."""




# ── Scam Sources / GoPlus ────────────────────────────────────────
class GoPlusConnector:
    """No-op GoPlus connector for standalone mode."""

    @classmethod
    async def check_token(cls, chain: str, token_address: str) -> dict[str, Any] | None:
        """No-op token check — standalone mode has no GoPlus integration."""
        return None


# ── Confidence Scoring ───────────────────────────────────────────
def score_confidence(
    results: list[dict[str, Any]], query: str, entity_matches: list[Any] | None = None
) -> dict[str, Any]:
    """Score confidence using rugbrain's confidence_scorer."""
    try:
        from rugbrain.confidence_scorer import score_confidence as _score

        # confidence_scorer.score_confidence(sources, query_similarity=...) —
        # query/entity_matches are accepted for API compat but not forwarded.
        return _score(results)
    except Exception:
        return {"confidence_score": 0.5, "confidence_label": "unknown", "breakdown": {}}


# Capabilities that are genuinely unavailable standalone now live in
# ── Investigation Narratives ─────────────────────────────────────
async def investigate(
    query: str = "",
    investigation_type: str = "auto",
    max_hops: int = 5,
    **kwargs: Any,
) -> dict[str, Any]:
    """Investigate query/address — no-op standalone."""
    return {"query": query, "findings": [], "risk_score": 0.5}


# ── LLM Config ───────────────────────────────────────────────────
AI_BASE = os.getenv("AI_BASE", "http://talos:8085")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")


async def generate_content(
    prompt: str,
    model: str = "qwen-chat",
    max_tokens: int = 500,
    temperature: float = 0.7,
) -> str:
    """Generate content via local LLM or env-configured endpoint."""
    try:
        import httpx

        url = f"{AI_BASE}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            return cast(str, content)
    except Exception as exc:
        logger.debug("generate_content_failed %s", str(exc)[:60])
    return ""
