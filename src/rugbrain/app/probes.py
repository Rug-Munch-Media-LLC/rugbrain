"""Dependency probes — Qdrant, Qdrant schema, Redis.

One job: answer "is this dependency healthy right now?" and return a bool (or
a small amount of measured data). Each probe is independently failure-tolerant
so a missing dependency never raises out of ``/health``.
"""

from __future__ import annotations

import logging
import os

import httpx

from rugbrain.app.health_state import STATE

logger = logging.getLogger(__name__)

#: Collections that must exist at the canonical production vector dimension.
CANONICAL_COLLECTIONS = ("news_v2", "rag_documents")


async def probe_qdrant(base_url: str, timeout: float = 5.0) -> bool:  # noqa: ASYNC109
    """Return True when the Qdrant HTTP root answers 200."""
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            r = await client.get("/")
        ok = r.status_code == 200
    except Exception as exc:
        logger.warning("qdrant_probe failed: %s", exc)
        return False
    logger.info("qdrant_probe status=%s", "ok" if ok else "fail")
    return ok


async def probe_qdrant_schema(base_url: str, timeout: float = 3.0) -> bool:  # noqa: ASYNC109
    """Verify the live canonical collections use the production vector dim."""
    from rugbrain.dim_policy import COLLECTION_DIM
    from rugbrain.qdrant_store import dense_vector_dimensions

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            for collection in CANONICAL_COLLECTIONS:
                response = await client.get(f"/collections/{collection}")
                if response.status_code != 200:
                    logger.warning(
                        "qdrant_schema_probe_failed collection=%s status=%d",
                        collection,
                        response.status_code,
                    )
                    return False
                params = response.json().get("result", {}).get("config", {}).get("params", {}) or {}
                dimensions = dense_vector_dimensions(params)
                if not dimensions or COLLECTION_DIM not in dimensions:
                    logger.error(
                        "qdrant_schema_incompatible collection=%s configured_dims=%s expected=%d",
                        collection,
                        sorted(dimensions),
                        COLLECTION_DIM,
                    )
                    return False
        return True
    except Exception as exc:
        logger.warning("qdrant_schema_probe_failed error=%s", exc)
        return False


async def probe_redis() -> bool:
    """Return True when Redis answers PING. Never raises."""
    import redis.asyncio as aioredis

    try:
        client = aioredis.Redis(
            host=STATE.get("redis_host") or "localhost",
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )
        await client.ping()
        await client.close()
    except Exception as exc:
        logger.warning("redis_probe failed: %s", exc)
        return False
    logger.info("redis_probe status=ok")
    return True


async def redis_queue_depth() -> int:
    """Scanner->RAG ingest queue backlog. ``-1`` when it cannot be read."""
    import redis.asyncio as aioredis

    try:
        client = aioredis.Redis(
            host=STATE.get("redis_host") or "localhost",
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )
        try:
            depth = int(await client.llen("rmi:rag:findings_queue") or 0)
        finally:
            await client.close()
        return depth
    except Exception:
        return -1


async def ensure_canonical_schema(base_url: str) -> bool:
    """Create the canonical collections at the production dim if absent."""
    from rugbrain.dim_policy import COLLECTION_DIM
    from rugbrain.qdrant_store import _ensure_collection

    try:
        schema_ok = True
        async with httpx.AsyncClient(base_url=base_url, timeout=5) as client:
            for collection in CANONICAL_COLLECTIONS:
                ok = await _ensure_collection(collection, COLLECTION_DIM, client)
                logger.info(
                    "qdrant_canonical_collection collection=%s dim=%d ok=%s",
                    collection,
                    COLLECTION_DIM,
                    ok,
                )
                schema_ok = schema_ok and ok
        return schema_ok
    except Exception as exc:
        logger.warning("qdrant_canonical_schema_check failed: %s", exc)
        return False
