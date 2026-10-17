"""Decoupled Qwen3 Reranker 8B client — the single ranking entry point.

Self-hosts a llama.cpp --rerank server on Talos port 8003. Degrades gracefully
when the server is down. Every caller funnels through :func:`rerank`, so the
per-document token ceiling is applied exactly once, here.

Siblings, one job each: ``rerank_health`` (health cache + probe + loop),
``rerank_instructions`` (per-task templates), ``rerank_compat`` (the
backward-compatible ``Reranker`` class and ``rerank_tiered``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

from rugbrain import provider_breaker
from rugbrain.rerank_compat import (
    RERANKER_ENABLED,
    RERANKER_URL,
    Reranker,
    get_reranker,
    rerank_tiered,
)
from rugbrain.rerank_health import _fast_health, get_reranker_health, reranker_health_loop
from rugbrain.rerank_health import _mark as _mark_health
from rugbrain.rerank_instructions import (
    RERANK_INSTRUCTIONS,
    rerank_alerts,
    rerank_instructed,
    rerank_kol_tweets,
)
from rugbrain.rerank_lanes import hosted_enabled, hosted_rerank  # multi-provider chain (2026-11)
from rugbrain.token_budget import budget_documents

logger = logging.getLogger(__name__)

RERANKER_TIMEOUT = float(os.getenv("RERANKER_TIMEOUT", "30"))
# First-hit on the CPU 0.6B lane measured 7.8s cold (2026-08-25); a 20s
# caller-side cap made every consumer rerank fail-open. 45s covers cold
# start + 20-doc batches with margin.
RERANK_CALL_TIMEOUT = float(os.getenv("RERANK_CALL_TIMEOUT", "45"))

# Named in __all__ so ruff keeps the re-export block (F401); these live in
# sibling modules and are re-exported so every existing import path resolves.
__all__ = [
    "RERANKER_ENABLED",
    "RERANKER_TIMEOUT",
    "RERANKER_URL",
    "RERANK_CALL_TIMEOUT",
    "RERANK_INSTRUCTIONS",
    "Reranker",
    "get_reranker",
    "get_reranker_health",
    "rerank",
    "rerank_alerts",
    "rerank_batch",
    "rerank_instructed",
    "rerank_kol_tweets",
    "rerank_tiered",
    "reranker_health_loop",
]


def _attach_documents(results: list[dict[str, Any]], documents: list[str]) -> None:
    """Back-fill the original document text onto raw results, in place."""
    for r in results:
        idx = r.get("index")
        r["document"] = documents[idx] if isinstance(idx, int) and idx < len(documents) else ""


async def _hosted_rung(query: str, documents: list[str], top_n: int) -> list[dict[str, Any]] | None:
    """Hosted DeepInfra rung — None when unavailable so the local lane runs."""
    if not hosted_enabled() or provider_breaker.is_dead("hosted-rerank"):
        return None
    try:
        return await hosted_rerank(query, documents, top_n=top_n)
    except Exception as exc:
        if any(c in str(exc) for c in ("401", "402", "403")):
            provider_breaker.mark_dead("hosted-rerank")
        logger.warning("hosted rerank failed (%s) — local lane", exc)
        return None


async def rerank(
    query: str,
    documents: list[str],
    top_k: int = 10,
    *,
    raw: bool = False,
) -> list[dict[str, Any]]:
    """Rank documents by relevance to query using the Qwen3 reranker.

    Every caller funnels through here, so the per-document token ceiling is
    applied once, here, rather than at each entry point. Measured 2026-08-28
    against the real corpus: live chunks average ~141 tokens, so the 768-token
    ceiling is free in practice and bounds the worst case. See
    ``rugbrain.token_budget``.

    Args:
        query: The search query or instruction.
        documents: List of document strings to rank.
        top_k: Number of top results to return (default 10).
        raw: If True, return ALL results with scores instead of just top_k.

    Returns:
        List of dicts with keys: index, relevance_score, (and document if raw)

    Degrades gracefully: returns empty list if server is down.
    """
    documents = budget_documents(documents)
    if not RERANKER_ENABLED or not documents:
        return []

    # Live lane: hosted DeepInfra first (same 8B family; ~0.5-2s per pool —
    # the retired 0.6B speed tier's replacement, 2026-09-14).  The local 8B
    # below remains the fallback and stays primary for batch/deep callers.
    hosted_results = await _hosted_rung(query, documents, 0 if raw else top_k)
    if hosted_results is not None:
        if raw:
            _attach_documents(hosted_results, documents)
        return hosted_results

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

            results: list[dict[str, Any]] = resp.json().get("results", [])

            # Cache health
            await _mark_health(True, (t1 - t0) * 1000)

            results.sort(key=lambda r: r["relevance_score"], reverse=True)
            if raw:
                _attach_documents(results, documents)
            return results[:top_k]

    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
        logger.debug("reranker_unavailable: %s", exc)
        await _mark_health(False)
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

    return await asyncio.gather(*[_one(q, d) for q, d in queries_and_docs])
