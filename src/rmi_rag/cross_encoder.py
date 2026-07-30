"""
Cross-Encoder Reranker — Ollama qwen3-embed:4b Cosine Approximation
====================================================================

Embeds query and each document separately via Ollama's qwen3-embed:4b, then
ranks by cosine similarity.  This is NOT a true cross-encoder (no
joint query+document scoring), but provides a pragmatic relevance
signal that often outperforms pure vector search alone.

Primary flow:
  1. Embed query via Ollama /api/embeddings (qwen3-embed:4b, 2560d)
  2. Embed each document text via Ollama
  3. Compute cosine similarity between query and each doc vector
  4. Return top_k sorted by similarity

Fallback: if the existing ``CryptoEmbedder`` pipeline is available,
use that instead of raw Ollama calls for better robustness.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://172.19.0.1:11434")
OLLAMA_MODEL = os.getenv("RERANK_MODEL", "qwen3-embed:4b")
OLLAMA_DIMS = 2560


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class OllamaReranker:
    """Cross-encoder reranker using Ollama qwen3-embed:4b embeddings + cosine similarity.

    Singleton via ``OllamaReranker.get_reranker()``.
    """

    _instance: OllamaReranker | None = None
    _lock = asyncio.Lock()

    # ── singleton ─────────────────────────────────────────────────

    @classmethod
    async def get_reranker(cls) -> OllamaReranker:
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._ollama_url = OLLAMA_URL.rstrip("/")
        self._model = OLLAMA_MODEL
        self._dims = OLLAMA_DIMS

    # ── embedding ────────────────────────────────────────────────

    async def _embed(self, text: str) -> list[float]:
        """Embed a single text via Ollama's /api/embeddings."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._ollama_url}/api/embeddings",
                json={"model": self._model, "prompt": text[:500]},
            )
            resp.raise_for_status()
            vec: list[float] = resp.json().get("embedding", [])
            if len(vec) != self._dims:
                raise RuntimeError(
                    f"Ollama returned {len(vec)}d vector for model {self._model}, "
                    f"expected {self._dims}d"
                )
            return vec

    async def _embed_via_pipeline(self, text: str) -> list[float]:
        """Embed via the existing CryptoEmbedder pipeline (fallback)."""
        from rmi_rag.crypto_embeddings import get_embedder

        embedder = await get_embedder()
        vec = await embedder.embed_query(text)
        return vec

    # ── public API ───────────────────────────────────────────────

    async def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Re-rank *documents* against *query* using Ollama qwen3-embed:4b cosine similarity.

        Parameters
        ----------
        query:
            The user query string.
        documents:
            List of document dicts, each containing at least ``"content"``
            or ``"text"`` and optionally ``"similarity"``.
        top_k:
            Number of top results to return.

        Returns
        -------
        list[dict]
            Documents sorted by cross_score descending, augmented with
            ``cross_score``, ``combined_score``, and ``rerank_method`` keys.
        """
        if not documents:
            return []

        top_k = min(top_k, len(documents))
        t0 = time.time()

        # ── embed query (try Ollama, fall back to pipeline) ──
        query_vec: list[float] | None = None
        try:
            query_vec = await self._embed(query)
        except Exception:
            logger.warning(
                "Ollama reranker: bge-m3 query embedding failed, trying CryptoEmbedder fallback"
            )
            try:
                query_vec = await self._embed_via_pipeline(query)
            except Exception:
                logger.exception("Ollama reranker: all embedding paths failed")
                return sorted(
                    documents,
                    key=lambda x: float(x.get("similarity", 0) or 0),
                    reverse=True,
                )[:top_k]

        # ── embed documents and score ──
        scored: list[tuple[dict[str, Any], float]] = []
        for doc in documents:
            text = doc.get("content") or doc.get("text", "")
            if not text:
                scored.append((doc, 0.0))
                continue
            try:
                doc_vec = await self._embed(text)
                sim = _cosine_similarity(query_vec, doc_vec)
                scored.append((doc, sim))
            except Exception:
                # Try pipeline fallback per-document
                try:
                    doc_vec = await self._embed_via_pipeline(text)
                    sim = _cosine_similarity(query_vec, doc_vec)
                    scored.append((doc, sim))
                except Exception:
                    logger.debug("Ollama reranker: doc embedding failed, using similarity=0")
                    scored.append((doc, 0.0))

        scored.sort(key=lambda x: x[1], reverse=True)

        duration_ms = (time.time() - t0) * 1000

        # ── observability ──
        try:
            from rmi_rag.observability import trace_reranker

            trace_reranker(
                duration_ms=duration_ms,
                doc_count=len(documents),
                top_k=top_k,
                model="ollama/bge-m3",
            )
        except Exception:
            logger.debug("Ollama reranker: observability trace failed", exc_info=True)

        # ── build results ──
        results: list[dict[str, Any]] = []
        for doc, score in scored[:top_k]:
            entry = dict(doc)
            vsim = float(doc.get("similarity", 0) or 0)
            entry["cross_score"] = round(score, 6)
            entry["combined_score"] = round(0.6 * score + 0.4 * vsim, 6)
            entry["rerank_method"] = "ollama-cosine"
            entry["rerank_duration_ms"] = round(duration_ms, 1)
            results.append(entry)

        logger.info(
            "Ollama reranker: %d docs -> top-%d in %.1f ms",
            len(documents),
            top_k,
            duration_ms,
        )
        return results

    def health_check(self) -> dict[str, Any]:
        return {
            "model": self._model,
            "ollama_url": self._ollama_url,
            "dims": self._dims,
            "method": "cosine-similarity",
        }


async def get_reranker() -> OllamaReranker:
    """Async convenience function to obtain the singleton OllamaReranker."""
    return await OllamaReranker.get_reranker()


async def rerank(query: str, documents: list[dict], top_k: int = 5) -> list[dict]:
    """Convenience wrapper — rerank documents against query."""
    reranker = OllamaReranker()
    return await reranker.rerank(query, documents, top_k)
