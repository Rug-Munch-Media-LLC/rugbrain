"""Decoupled Qwen3 Reranker 8B client — stand-alone, zero rugbrain imports.

Self-hosts a llama.cpp --rerank server on Talos port 8003.
Degrades gracefully when server is down.

Usage:
    from rugbrain.reranker import rerank, get_reranker_health

    # Rank documents by relevance to query
    results = await rerank(
        query="BTC whale moving to exchange",
        documents=["text1", "text2", "text3"],
        top_k=3
    )

    # Check health
    health = await get_reranker_health()
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RERANKER_URL = os.getenv("RERANKER_URL", "http://127.0.0.1:8003")
RERANKER_TIMEOUT = float(os.getenv("RERANKER_TIMEOUT", "30"))
RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "true").lower() == "true"

_health: dict[str, Any] = {"ok": False, "last_check": 0.0, "latency_ms": 0}
_lock = asyncio.Lock()


# ── Backward-compatible exports ──────────────────────────────────


class Reranker:
    """Backward-compatible Reranker class wrapping the new async API.

    Usage:
        r = Reranker(url="http://127.0.0.1:8003")
        results = await r.rerank(query, docs, top_k=5)
    """

    def __init__(self, url: str | None = None):
        self._url = url or RERANKER_URL

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Rank documents — wraps top-level rerank function."""
        return await _rerank_impl(query, documents, top_k, url=self._url)

    async def health(self) -> dict[str, Any]:
        """Check reranker health."""
        if not RERANKER_ENABLED:
            return {"ok": False, "enabled": False}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._url}/health")
                if resp.status_code == 200:
                    data = resp.json()
                    return {"ok": True, "model": data.get("model", "unknown")}
                return {"ok": False, "status_code": resp.status_code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def get_reranker() -> Reranker:
    """Backward-compatible: get a default Reranker instance."""
    return Reranker()


# ── Main async API (new, decoupled) ─────────────────────────────


async def rerank(
    query: str,
    documents: list[str],
    top_k: int = 10,
    *,
    raw: bool = False,
) -> list[dict[str, Any]]:
    """Rank documents by relevance to query using the Qwen3 reranker.

    Args:
        query: The search query or instruction.
        documents: List of document strings to rank.
        top_k: Number of top results to return (default 10).
        raw: If True, return ALL results with scores instead of just top_k.

    Returns:
        List of dicts with keys: index, relevance_score, (and document if raw)

    Degrades gracefully: returns empty list if server is down.
    """
    if not RERANKER_ENABLED or not documents:
        return []

    # Health check with caching (don't hammer a down server)
    if not await _fast_health():
        return []

    try:
        async with httpx.AsyncClient(timeout=RERANKER_TIMEOUT) as client:
            payload: dict[str, Any] = {
                "query": query,
                "documents": documents,
                "top_n": top_k if not raw else len(documents),
            }
            t0 = time.monotonic()
            resp = await client.post(f"{RERANKER_URL}/rerank", json=payload)
            t1 = time.monotonic()

            if resp.status_code != 200:
                logger.warning("reranker_status=%d", resp.status_code)
                return []

            data = resp.json()
            results = data.get("results", [])

            # Cache health
            async with _lock:
                _health["ok"] = True
                _health["last_check"] = time.time()
                _health["latency_ms"] = round((t1 - t0) * 1000, 1)

            # Sort by relevance descending
            results.sort(key=lambda r: r["relevance_score"], reverse=True)

            if raw:
                # Attach original documents
                for r in results:
                    idx = r["index"]
                    r["document"] = documents[idx] if idx < len(documents) else ""

            return results[:top_k]

    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
        logger.debug("reranker_unavailable: %s", exc)
        async with _lock:
            _health["ok"] = False
            _health["last_check"] = time.time()
        return []


async def rerank_batch(
    queries_and_docs: list[tuple[str, list[str]]],
    top_k: int = 5,
    *,
    concurrency: int = 3,
) -> list[list[dict[str, Any]]]:
    """Batch rerank multiple query-document sets concurrently.

    Args:
        queries_and_docs: List of (query, [documents]) tuples.
        top_k: Top results per query.
        concurrency: Max concurrent requests (default 3).

    Returns:
        List of results (one per input tuple).
    """
    sem = asyncio.Semaphore(concurrency)

    async def _one(query: str, docs: list[str]) -> list[dict[str, Any]]:
        async with sem:
            return await rerank(query, docs, top_k=top_k)

    tasks = [_one(q, d) for q, d in queries_and_docs]
    return await asyncio.gather(*tasks)


async def _fast_health() -> bool:
    """Fast cached health check — avoids hammering a down server."""
    async with _lock:
        if _health["ok"] and (time.time() - _health["last_check"]) < 30:
            return True
        if not _health["ok"] and (time.time() - _health["last_check"]) < 60:
            return False
    return True  # Allow the actual request to probe


async def get_reranker_health() -> dict[str, Any]:
    """Check if the reranker server is healthy.

    Returns:
        Dict with ok (bool), latency_ms (float), model (str or None).
    """
    if not RERANKER_ENABLED:
        return {"ok": False, "enabled": False, "reason": "RERANKER_ENABLED=false"}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            t0 = time.monotonic()
            resp = await client.get(f"{RERANKER_URL}/health")
            t1 = time.monotonic()

            if resp.status_code == 200:
                data = resp.json()
                async with _lock:
                    _health["ok"] = True
                    _health["last_check"] = time.time()
                    _health["latency_ms"] = round((t1 - t0) * 1000, 1)

                return {
                    "ok": True,
                    "enabled": True,
                    "latency_ms": _health["latency_ms"],
                    "model": data.get("model", "unknown"),
                }

            async with _lock:
                _health["ok"] = False
                _health["last_check"] = time.time()
            return {"ok": False, "enabled": True, "status_code": resp.status_code}

    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        async with _lock:
            _health["ok"] = False
            _health["last_check"] = time.time()
        return {"ok": False, "enabled": True, "error": str(exc)}


async def reranker_health_loop() -> None:
    """Background health check loop — updates _health cache every 60s."""
    while True:
        await get_reranker_health()
        await asyncio.sleep(60)
