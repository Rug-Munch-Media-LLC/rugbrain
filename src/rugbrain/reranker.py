"""Decoupled Qwen3 Reranker 4B client — stand-alone, zero rugbrain imports.

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

from rugbrain.rerank_hosted import hosted_enabled, hosted_rerank
from rugbrain.token_budget import budget_documents

logger = logging.getLogger(__name__)

RERANKER_URL = os.getenv("RERANKER_URL", "http://127.0.0.1:8003")
# 'fast'/'deep' both resolve to the canonical 4B lane: the 0.6B consumer
# tier is retired (2026-08-29) and its endpoint is gone (2026-09-14).
RERANK_FAST_URL = os.getenv("RERANK_FAST_URL", RERANKER_URL)
RERANKER_TIMEOUT = float(os.getenv("RERANKER_TIMEOUT", "30"))
# First-hit on the CPU 0.6B lane measured 7.8s cold (2026-08-25); a 20s
# caller-side cap made every consumer rerank fail-open. 45s covers cold
# start + 20-doc batches with margin.
RERANK_CALL_TIMEOUT = float(os.getenv("RERANK_CALL_TIMEOUT", "45"))
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
        return await rerank(query, documents, top_k)

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


# ── Instruction-aware reranking ────────────────────────────────────
# Instruction templates for per-task reranking
RERANK_INSTRUCTIONS: dict[str, str] = {
    "content_digest": (
        "Rank these crypto market signals by importance for a trader dashboard "
        "prioritizing: unusual whale activity, imminent exchange risks, "
        "liquidation cascades, and DeFi protocol anomalies over routine price movements"
    ),
    "alert_filter": (
        "Identify which of these alerts represents a real imminent threat requiring "
        "immediate attention versus routine market activity noise. Prioritize: "
        "exchange withdrawal suspensions, large whale movements to exchanges, "
        "oracle price deviations, and protocol insolvency signals"
    ),
    "kol_scam": (
        "Analyze this KOL's tweet for signs of scam promotion: excessive hype "
        "without substance, shilling low-cap tokens, coordinated engagement "
        "patterns, promises of guaranteed returns, and pressure to act quickly. "
        "Flag as suspicious if multiple scam indicators are present"
    ),
    "search": (
        "Rank these documents by relevance to the user's search intent "
        "prioritizing: specific data points, actionable intelligence, recent "
        "events, and verified on-chain activity over general commentary"
    ),
    "wallet_risk": (
        "Evaluate the risk level of these wallet movements prioritizing: "
        "funds moving to exchanges known for wash trading, large outflows from "
        "shady exchanges, transfers to mixers or privacy wallets, and "
        "accumulation by known scam-affiliated addresses"
    ),
}


async def rerank_instructed(
    task_type: str,
    query: str,
    documents: list[str],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Rerank with instruction-aware prompt for specific task types.

    Args:
        task_type: One of 'content_digest', 'alert_filter', 'kol_scam', 'search', 'wallet_risk'.
        query: The search query.
        documents: Documents to rank.
        top_k: Number of results to return.

    Uses instruction template to customize scoring per task.
    Falls back to standard rerank if task_type is unknown.
    """
    instruction = RERANK_INSTRUCTIONS.get(task_type)
    if instruction:
        # Instruction-aware: prepend instruction to query
        instructed_query = f"[INST] {instruction} [/INST] Query: {query}"
        return await rerank(instructed_query, documents, top_k=top_k)
    return await rerank(query, documents, top_k=top_k)


async def rerank_alerts(
    alerts: list[dict[str, Any]],
    top_k: int = 20,
) -> list[dict[str, Any]]:
    """Score alerts by real threat level using instruction-aware reranking.

    Args:
        alerts: List of alert dicts with 'title' and 'message' keys.
        top_k: Return top-k ranked alerts.

    Returns alerts sorted by threat score descending.
    """
    if not alerts:
        return []

    docs = [f"{a.get('title', '')}: {str(a.get('message', ''))[:200]}" for a in alerts]
    results = await rerank_instructed(
        "alert_filter",
        "Which alerts represent real imminent threats?",
        docs,
        top_k=top_k,
    )

    # Map scores back to alerts
    scored: list[dict[str, Any]] = []
    for r in results:
        idx = r["index"]
        if idx < len(alerts):
            a = dict(alerts[idx])
            a["_rerank_score"] = r["relevance_score"]
            scored.append(a)
    scored.sort(key=lambda x: x.get("_rerank_score", 0), reverse=True)
    return scored


async def rerank_kol_tweets(
    tweets: list[dict[str, Any]],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Score KOL tweets by scam likelihood.

    Args:
        tweets: List of tweet dicts with 'text' and 'author' keys.
        top_k: Return top-k most suspicious tweets.

    Returns tweets sorted by scam score descending.
    """
    if not tweets:
        return []

    docs = [f"@{t.get('author', '')}: {t.get('text', '')[:200]}" for t in tweets]
    results = await rerank_instructed(
        "kol_scam",
        "Which tweets show signs of scam promotion?",
        docs,
        top_k=top_k,
    )

    scored: list[dict[str, Any]] = []
    for r in results:
        idx = r["index"]
        if idx < len(tweets):
            t = dict(tweets[idx])
            t["_scam_score"] = r["relevance_score"]
            scored.append(t)
    scored.sort(key=lambda x: x.get("_scam_score", 0), reverse=True)
    return scored


def get_reranker() -> Reranker:
    """Backward-compatible: get a default Reranker instance."""
    return Reranker()


# ── Main async API (new, decoupled) ─────────────────────────────


async def rerank_tiered(
    query: str,
    documents: list[str],
    top_k: int = 10,
    *,
    tier: str = "fast",
    raw: bool = False,
) -> list[dict[str, Any]]:
    """Tier-labeled rerank: both 'fast' and 'deep' serve the 4B lane
    (:8003; 0.6B retired 2026-09-14). Fail-open: returns original order when
    the lane is unavailable."""
    url = RERANK_FAST_URL if tier == "fast" else RERANKER_URL
    if not RERANKER_ENABLED or not documents:
        return []
    try:
        async with httpx.AsyncClient(timeout=RERANK_CALL_TIMEOUT) as client:
            payload: dict[str, Any] = {
                "query": query,
                "documents": documents,
                "top_n": top_k if not raw else len(documents),
            }
            resp = await client.post(f"{url}/rerank", json=payload)
            if resp.status_code != 200:
                logger.warning("reranker_status=%d url=%s", resp.status_code, url)
                return []
            data = resp.json()
            results: list[dict[str, Any]] = data.get("results", [])
            results.sort(key=lambda r: r["relevance_score"], reverse=True)
            return results if raw else results[:top_k]
    except Exception as exc:
        logger.warning("reranker_unreachable url=%s err=%s", url, str(exc)[:80])
        return []


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

    # Live lane: hosted DeepInfra first (same 4B family; ~0.5-2s per pool —
    # the retired 0.6B speed tier's replacement, 2026-09-14).  The local 4B
    # below remains the fallback and stays primary for batch/deep callers.
    if hosted_enabled():
        hosted_results = await hosted_rerank(query, documents, top_n=(0 if raw else top_k))
        if hosted_results is not None:
            if raw:
                for r in hosted_results:
                    idx = r.get("index")
                    r["document"] = (
                        documents[idx]
                        if isinstance(idx, int) and idx < len(documents)
                        else ""
                    )
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

            data = resp.json()
            results: list[dict[str, Any]] = data.get("results", [])

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
