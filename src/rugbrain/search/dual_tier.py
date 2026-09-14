"""Tier-labeled retrieval — all lanes serve the Qwen3 4B family (2026-09-14).

HISTORY: this module routed between a 0.6B "speed" tier (:8007/:8008) and a
4B "deep" tier. The 0.6B tier was retired (2026-08-29) and its endpoints
removed (2026-09-14); both labels now resolve to the canonical 4B lanes and
the speed labels are kept only for API compatibility.

Tier endpoints (llama.cpp on Talos, all bound to 127.0.0.1):
  embed    :8004  Qwen3-Embedding-4B Q8_0  (2560-dim — the ONLY embed lane)
  rerank   :8003  Qwen3-Reranker-4B  Q6_K
  chat     :8009  Qwen3-8B-Instruct Q4_K_M

Fail-open chain: primary -> fallback -> no-context (callers keep working,
tagged). Reranks honor the shared vertical instruction library via the
query-slot technique (see rugbrain.search.rerank.apply_instruction).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from rugbrain.search.rerank import apply_instruction

# ── configuration ────────────────────────────────────────────────────────────
_DEEP_EMBED = os.getenv("DEEP_EMBED_URL", "http://127.0.0.1:8004")
_DEEP_RERANK = os.getenv("DEEP_RERANK_URL", "http://127.0.0.1:8003")
ENDPOINTS = {
    "chat": os.getenv("CHAT_URL", "http://127.0.0.1:8009"),
    "deep_embed": _DEEP_EMBED,
    "deep_rerank": _DEEP_RERANK,
    # speed_* resolve to the canonical 4B lanes: the 0.6B tier is retired
    # (2026-08-29) and its endpoints are gone (2026-09-14). Explicit
    # SPEED_* env overrides still win if a speed server is ever revived.
    "speed_embed": os.getenv("SPEED_EMBED_URL", _DEEP_EMBED),
    "speed_rerank": os.getenv("SPEED_RERANK_URL", _DEEP_RERANK),
}
QDRANT_URL = os.getenv("QDRANT_URL", "http://rmi-qdrant-v2:6334")
COLLECTION = os.getenv("RAG_COLLECTION", "crypto_intelligence")

ROUTING_THRESHOLD_WORDS = int(os.getenv("ROUTING_THRESHOLD_WORDS", "45"))
SPEED_RECALL = int(os.getenv("SPEED_RECALL", "80"))
DEEP_RECALL = int(os.getenv("DEEP_RECALL", "30"))
TOP_CONTEXT = int(os.getenv("TOP_CONTEXT", "5"))
HTTP_TIMEOUT_S = float(os.getenv("DUAL_TIER_TIMEOUT", "60"))


@dataclass
class RetrievalResult:
    """Outcome of a dual-tier retrieval pass."""

    contexts: list[dict[str, Any]] = field(default_factory=list)
    tier: str = "none"  # speed | deep | none
    fallback_used: bool = False  # True when the primary tier failed
    latency_ms: float = 0.0
    recall_count: int = 0


def pick_tier(query: str) -> str:
    """Route by query complexity: short consumer queries -> speed tier."""
    return "speed" if len(query.split()) < ROUTING_THRESHOLD_WORDS else "deep"


class DualTierRetriever:
    """Word-count routed retrieval over the two Qwen3 embedding/rerank tiers."""

    def __init__(
        self,
        qdrant: Any = None,
        http: aiohttp.ClientSession | None = None,
    ) -> None:
        self._qdrant = qdrant
        self._http = http

    # ── internals ─────────────────────────────────────────────────────────
    def _qdrant_client(self) -> Any:
        if self._qdrant is None:
            from qdrant_client import AsyncQdrantClient

            self._qdrant = AsyncQdrantClient(url=QDRANT_URL)
        return self._qdrant

    @staticmethod
    async def _embed(http: aiohttp.ClientSession, url: str, text: str) -> list[float]:
        async with http.post(f"{url}/v1/embeddings", json={"input": text}) as resp:
            resp.raise_for_status()
            data: list[dict[str, Any]] = (await resp.json())["data"]
            vec: list[float] = data[0]["embedding"]
            return vec

    @staticmethod
    async def _rerank(
        http: aiohttp.ClientSession, url: str, query: str, docs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not docs:
            return []
        async with http.post(
            f"{url}/rerank", json={"query": query, "documents": [d["text"] for d in docs]}
        ) as resp:
            resp.raise_for_status()
            results = await resp.json()
        ordered = sorted(results["results"], key=lambda r: r["relevance_score"], reverse=True)
        return [docs[r["index"]] for r in ordered[:TOP_CONTEXT]]

    async def _tier_retrieve(
        self,
        http: aiohttp.ClientSession,
        tier: str,
        inst_query: str,
        vector_name: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        embed_url = ENDPOINTS[f"{tier}_embed"]
        rerank_url = ENDPOINTS[f"{tier}_rerank"]
        vec = await self._embed(http, embed_url, inst_query)
        hits = await self._qdrant_client().query_points(
            collection_name=COLLECTION, query=vec, using=vector_name, limit=limit
        )
        candidates = [
            {
                "text": p.payload.get("text", ""),
                "meta": p.payload.get("metadata", {}),
                "id": str(p.id),
            }
            for p in hits.points
        ]
        return await self._rerank(http, rerank_url, inst_query, candidates)

    # ── public API ────────────────────────────────────────────────────────
    async def retrieve(self, query: str, top_k: int = TOP_CONTEXT) -> RetrievalResult:
        """Route a query across the tiers and return ranked contexts.

        Fail-open chain: primary tier -> other tier -> empty result.
        Never raises for tier unavailability; only for programmer errors.
        """
        t0 = time.perf_counter()
        primary = pick_tier(query)
        inst_query = apply_instruction(query)

        plan = {
            "speed": ("speed_vector", SPEED_RECALL),
            "deep": ("qwen_vector", DEEP_RECALL),
        }
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_S)
        http = self._http or aiohttp.ClientSession(timeout=timeout)
        try:
            try:
                vector_name, limit = plan[primary]
                contexts = await self._tier_retrieve(http, primary, inst_query, vector_name, limit)
                return RetrievalResult(
                    contexts=contexts[:top_k],
                    tier=primary,
                    latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                    recall_count=len(contexts),
                )
            except (aiohttp.ClientError, TimeoutError, Exception) as exc:
                fallback = "deep" if primary == "speed" else "speed"
                vector_name, limit = plan[fallback]
                try:
                    contexts = await self._tier_retrieve(
                        http, fallback, inst_query, vector_name, limit
                    )
                    return RetrievalResult(
                        contexts=contexts[:top_k],
                        tier=fallback,
                        fallback_used=True,
                        latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                        recall_count=len(contexts),
                    )
                except Exception:
                    print(f"[dual_tier] both tiers unavailable ({exc!r})")
                    return RetrievalResult(
                        tier="none", latency_ms=round((time.perf_counter() - t0) * 1000, 1)
                    )
        finally:
            if self._http is None:
                await http.close()

    async def generate(self, query: str, contexts: list[dict[str, Any]]) -> str:
        """Chat generation over retrieved contexts (:8009)."""
        payload = "\n\n".join(f"[Crypto Intel Reference]: {c['text']}" for c in contexts)
        body = {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the Terminal Intelligence Bot for a crypto intelligence "
                        "platform. Deliver strict, factual summaries derived directly from "
                        "the provided document contexts. Never invent addresses, balances, "
                        "or market data."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Context Documents:\n{payload}\n\nUser Question: {query}",
                },
            ],
            "temperature": 0.15,
            "stream": False,
        }
        timeout = aiohttp.ClientTimeout(total=300)
        async with (
            aiohttp.ClientSession(timeout=timeout) as http,
            http.post(f"{ENDPOINTS['chat']}/v1/chat/completions", json=body) as resp,
        ):
            resp.raise_for_status()
            return str((await resp.json())["choices"][0]["message"]["content"])

    async def ask(self, query: str) -> dict[str, Any]:
        """Convenience: retrieve + generate in one call."""
        result = await self.retrieve(query)
        answer = await self.generate(query, result.contexts) if result.contexts else ""
        return {
            "answer": answer,
            "tier": result.tier,
            "fallback_used": result.fallback_used,
            "latency_ms": result.latency_ms,
        }
