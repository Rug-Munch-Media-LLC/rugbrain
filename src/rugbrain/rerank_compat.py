"""Backward-compatible class + tier-lane surface for the Qwen3 reranker.

``Reranker`` predates the top-level async functions and is still imported by
``rugbrain.__init__`` and ``compat_retrieval``. ``rerank_tiered`` is the tier
labelled wrapper used by ``multi_embedder``. Both live here so
``rugbrain.reranker`` stays the single ranking job.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from rugbrain.rerank_endpoint import rerank_endpoint

logger = logging.getLogger(__name__)

# REQUIRED config, no default: the only baked default used to be the DeepInfra
# inference URL, which answers 402 (overdue invoices) since 2026-10-22 — a
# default that silently points at a dead paid lane is worse than no default.
# Deployments set RERANKER_URL (`.env.example` -> the local :8003 rerank lane).
RERANKER_URL = os.getenv("RERANKER_URL", "").rstrip("/")
# 'fast'/'deep' both resolve to the canonical 4B lane: the 0.6B consumer
# tier is retired (2026-08-29) and its endpoint is gone (2026-09-14).
RERANK_FAST_URL = os.getenv("RERANK_FAST_URL", RERANKER_URL)
RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "true").lower() == "true"
# First-hit on the CPU 0.6B lane measured 7.8s cold (2026-08-25); a 20s
# caller-side cap made every consumer rerank fail-open. 45s covers cold
# start + 20-doc batches with margin.
RERANK_CALL_TIMEOUT = float(os.getenv("RERANK_CALL_TIMEOUT", "45"))

__all__ = ["Reranker", "get_reranker", "rerank_tiered"]


class Reranker:
    """Backward-compatible Reranker class wrapping the new async API.

    Usage:
        r = Reranker(url=os.getenv("RERANKER_URL", ""))
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
        from rugbrain.reranker import rerank

        return await rerank(query, documents, top_k)

    async def health(self) -> dict[str, Any]:
        """Check reranker health."""
        if not RERANKER_ENABLED:
            return {"ok": False, "enabled": False}
        try:
            if not self._url:
                return {"ok": False, "error": "RERANKER_URL not configured"}
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
    if not url:
        logger.warning("reranker_url_unset tier=%s — set RERANKER_URL", tier)
        return []
    try:
        async with httpx.AsyncClient(timeout=RERANK_CALL_TIMEOUT) as client:
            payload: dict[str, Any] = {
                "query": query,
                "documents": documents,
                "top_n": top_k if not raw else len(documents),
            }
            resp = await client.post(rerank_endpoint(url), json=payload)
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
