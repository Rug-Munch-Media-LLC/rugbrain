"""Dual-tier RAG gateway — word-count routed retrieval for consumer chat.

Routing (per production design, ports adapted to Talos reality):
  < 45 words  -> SPEED tier : qwen3-embedding-8b (:8007) recall 80
                              -> Qwen3-Reranker-0.6B (:8008) -> top 5
  >= 45 words -> DEEP tier  : Qwen3-Embedding-8B   (:8004) recall 30
                              -> Qwen3-Reranker-8B   (:8003) -> top 5

Generation: qwen3-8b-instruct via llama.cpp :8009 (llama-chat.service).

Design notes:
  * Deep tier reuses the EXISTING production services (:8003/:8004) instead of
    duplicating them on new ports — saves ~10GB RAM on a shared host.
  * Both rerankers are instruction-aware via the query-slot technique
    (see rugbrain.search.rerank for the library); the same vertical
    instructions apply across tiers.
  * Fail-open: if a tier is unreachable the other tier serves the request.
"""

from __future__ import annotations

import os
import time
from typing import Any

import aiohttp
from qdrant_client import AsyncQdrantClient

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.getenv("RAG_COLLECTION", "crypto_intelligence")

ENDPOINTS = {
    "chat": os.getenv("CHAT_URL", "http://127.0.0.1:8009"),
    "deep_embed": os.getenv("DEEP_EMBED_URL", "http://127.0.0.1:8004"),
    "deep_rerank": os.getenv("DEEP_RERANK_URL", "http://127.0.0.1:8003"),
    # speed_* resolve to the canonical 4B lanes (0.6B retired 2026-09-14).
    "speed_embed": os.getenv("SPEED_EMBED_URL", "http://127.0.0.1:8004"),
    "speed_rerank": os.getenv("SPEED_RERANK_URL", "http://127.0.0.1:8003"),
}

ROUTING_THRESHOLD_WORDS = int(os.getenv("ROUTING_THRESHOLD_WORDS", "45"))
SPEED_RECALL = int(os.getenv("SPEED_RECALL", "80"))
DEEP_RECALL = int(os.getenv("DEEP_RECALL", "30"))
TOP_CONTEXT = int(os.getenv("TOP_CONTEXT", "5"))

CHAT_SYSTEM_PROMPT = (
    "You are the Terminal Intelligence Bot for a crypto intelligence platform. "
    "Deliver strict, factual summaries derived directly from the provided "
    "document contexts. Never invent addresses, balances, or market data."
)


class CryptoRAGGateway:
    def __init__(self, qdrant_url: str | None = None):
        self.db = AsyncQdrantClient(url=qdrant_url or QDRANT_URL)

    async def close(self) -> None:
        await self.db.close()

    # ── engines ───────────────────────────────────────────────────────────
    async def _embed(self, http: aiohttp.ClientSession, url: str, text: str) -> list[float]:
        async with http.post(f"{url}/v1/embeddings", json={"input": text}) as resp:
            resp.raise_for_status()
            data: list[dict[str, Any]] = (await resp.json())["data"]
            vec: list[float] = data[0]["embedding"]
            return vec

    async def _rerank(
        self, http: aiohttp.ClientSession, url: str, query: str, docs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not docs:
            return []
        async with http.post(
            f"{url}/rerank",
            json={"query": query, "documents": [d["text"] for d in docs]},
        ) as resp:
            resp.raise_for_status()
            results = await resp.json()
        ordered = sorted(results["results"], key=lambda r: r["relevance_score"], reverse=True)
        return [docs[r["index"]] for r in ordered[:TOP_CONTEXT]]

    # ── tiers ─────────────────────────────────────────────────────────────
    async def _retrieve(
        self, http: aiohttp.ClientSession, tier: str, query: str
    ) -> tuple[list[dict[str, Any]], str]:
        """Returns (contexts, tier_used). Fails open to the other tier."""
        if tier == "speed":
            embed_url, rerank_url, vector, limit = (
                ENDPOINTS["speed_embed"],
                ENDPOINTS["speed_rerank"],
                "speed_vector",
                SPEED_RECALL,
            )
        else:
            embed_url, rerank_url, vector, limit = (
                ENDPOINTS["deep_embed"],
                ENDPOINTS["deep_rerank"],
                "qwen_vector",
                DEEP_RECALL,
            )
        try:
            vec = await self._embed(http, embed_url, query)
            hits = await self.db.query_points(
                collection_name=COLLECTION, query=vec, using=vector, limit=limit
            )
            candidates = [
                {"text": (p.payload or {}).get("text", ""), "meta": (p.payload or {}).get("metadata", {})}
                for p in hits.points
            ]
            contexts = await self._rerank(http, rerank_url, query, candidates)
            return contexts, tier
        except (aiohttp.ClientError, TimeoutError) as exc:
            fallback = "deep" if tier == "speed" else "speed"
            print(f"[gateway] {tier} tier unavailable ({exc!r}) — failing open to {fallback}")
            if tier == "speed":
                return await self._retrieve(http, "deep", query)
            return [], f"{tier}-down"

    # ── public entry ──────────────────────────────────────────────────────
    async def execute_query(self, user_query: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        tier = "speed" if len(user_query.split()) < ROUTING_THRESHOLD_WORDS else "deep"

        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            contexts, used_tier = await self._retrieve(http, tier, user_query)

            context_payload = "\n\n".join(
                f"[Crypto Intel Reference]: {d['text']}" for d in contexts
            )
            chat_body = {
                "messages": [
                    {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Context Documents:\n{context_payload}\n\nUser Question: {user_query}",
                    },
                ],
                "temperature": 0.15,
                "stream": False,
            }
            async with http.post(
                f"{ENDPOINTS['chat']}/v1/chat/completions", json=chat_body
            ) as resp:
                resp.raise_for_status()
                answer = (await resp.json())["choices"][0]["message"]["content"]

        return {
            "answer": answer,
            "tier": used_tier,
            "contexts": contexts,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }


async def main() -> None:
    gw = CryptoRAGGateway()
    try:
        result = await gw.execute_query("is this token a rug pull?")
        print(f"[{result['tier']} | {result['latency_ms']}ms] {result['answer'][:300]}")
    finally:
        await gw.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
