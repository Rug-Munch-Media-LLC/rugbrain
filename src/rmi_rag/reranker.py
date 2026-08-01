"""
BGE Cross-Encoder Reranker — BAAI/bge-reranker-v2-m3
====================================================

True cross-encoder reranker for the RAG retrieval pipeline.

Unlike bi-encoders (which embed query and document separately), a
cross-encoder processes query+document together and produces a relevance
score for the pair. Applying it to the top-20 candidates from dense+sparse
hybrid search typically improves ranking quality by +15-20% NDCG.

Backends (in priority order):
  1. Ollama native ``/api/rerank`` — used when a reranker model
     (``bge-reranker-v2-m3``) is already loaded in the local Ollama.
  2. sentence-transformers ``CrossEncoder('BAAI/bge-reranker-v2-m3')``
     — CPU-friendly, downloads weights to HF cache on first use.
  3. Graceful no-op — if neither backend is available, documents are
     returned unchanged (sorted by their original score). Search never
     breaks because the reranker is unavailable.

Memory management:
  - Singleton pattern: the model loads once on first use and stays resident.
  - ``OLLAMA_KEEP_ALIVE=30m`` keeps the Ollama-managed model warm.
  - Model load time and process RSS are logged for capacity planning.

Environment variables:
  - ``RERANK_MODEL`` — Ollama reranker model name (default: bge-reranker-v2-m3)
  - ``RERANK_MODEL_NAME`` — HuggingFace model id (default: BAAI/bge-reranker-v2-m3)
  - ``RERANK_MAX_LENGTH`` — max tokens per pair (default: 512)
  - ``OLLAMA_URL`` — Ollama base URL (reused from embedder config)
"""

from __future__ import annotations

import asyncio
import logging
import os
import resource
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_RERANK_MODEL = os.getenv("RERANK_MODEL", "bge-reranker-v2-m3")
ST_MODEL_NAME = os.getenv("RERANK_MODEL_NAME", "BAAI/bge-reranker-v2-m3")
RERANK_MAX_LENGTH = int(os.getenv("RERANK_MAX_LENGTH", "512"))

try:  # Metrics are optional — never let observability break search
    from rmi_rag.metrics import rerank_latency, rerank_requests
except Exception:  # pragma: no cover - metrics module may not be installed
    rerank_latency = None
    rerank_requests = None


def _rss_mb() -> float:
    """Current max RSS of this process in MB (Linux)."""
    try:
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return 0.0


class Reranker:
    """Cross-encoder reranker with Ollama → sentence-transformers fallback.

    Singleton via ``Reranker.get_reranker()``. The heavy model is loaded
    lazily on the first ``rerank()`` call and kept in memory afterwards.
    """

    _instance: Reranker | None = None
    _lock = asyncio.Lock()

    # ── singleton ─────────────────────────────────────────────────

    @classmethod
    async def get_reranker(cls) -> Reranker:
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self, model_name: str = ST_MODEL_NAME, use_ollama: bool = True) -> None:
        """Initialize reranker. Tries Ollama first, falls back to sentence-transformers.

        Parameters
        ----------
        model_name:
            HuggingFace model id for the sentence-transformers fallback.
        use_ollama:
            If True, attempt Ollama's native ``/api/rerank`` backend first
            (requires a reranker model already present in Ollama).
        """
        self._st_model_name = model_name
        self._use_ollama = use_ollama
        self._st_model: Any | None = None
        self._st_loaded = False
        self._st_load_failed = False
        self._ollama_checked = False
        self._ollama_available = False
        self._load_time_s = 0.0

    # ── backend discovery ────────────────────────────────────────

    async def _ollama_has_reranker(self) -> bool:
        """Check whether a reranker model is already loaded in Ollama.

        Cached per process: the registry does not change at runtime.
        """
        if self._ollama_checked:
            return self._ollama_available
        self._ollama_checked = True
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{OLLAMA_URL}/api/tags")
                resp.raise_for_status()
                names = {m.get("name", "") for m in resp.json().get("models", [])}
                wanted = OLLAMA_RERANK_MODEL
                self._ollama_available = any(
                    n == wanted or n.split(":")[0] == wanted for n in names
                )
        except Exception as exc:
            logger.debug("ollama_rerank_probe_failed: %s", exc)
            self._ollama_available = False
        if not self._ollama_available:
            logger.info(
                "reranker: Ollama has no '%s' model — using sentence-transformers fallback",
                OLLAMA_RERANK_MODEL,
            )
        return self._ollama_available

    def _load_st_model(self) -> None:
        """Load the sentence-transformers CrossEncoder (once, lazily)."""
        if self._st_loaded or self._st_load_failed:
            return
        t0 = time.perf_counter()
        try:
            from sentence_transformers import CrossEncoder

            self._st_model = CrossEncoder(self._st_model_name, max_length=RERANK_MAX_LENGTH)
            self._st_loaded = True
            self._load_time_s = time.perf_counter() - t0
            logger.info(
                "reranker: loaded %s in %.1fs (RSS %.0f MB)",
                self._st_model_name,
                self._load_time_s,
                _rss_mb(),
            )
        except Exception as exc:
            self._st_load_failed = True
            logger.warning(
                "reranker: failed to load %s (%s) — reranking disabled, search unaffected",
                self._st_model_name,
                exc,
            )

    # ── scoring backends ─────────────────────────────────────────

    async def _score_ollama(self, query: str, documents: list[dict[str, Any]]) -> list[float]:
        """Score all (query, doc) pairs via Ollama native /api/rerank."""
        texts = [(doc.get("content") or doc.get("text") or "")[:2000] for doc in documents]
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/rerank",
                json={"model": OLLAMA_RERANK_MODEL, "query": query, "documents": texts},
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
        scores = [0.0] * len(documents)
        for item in results:
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < len(scores):
                scores[idx] = float(item.get("relevance_score", 0.0))
        return scores

    def _score_st(self, query: str, documents: list[dict[str, Any]]) -> list[float]:
        """Score all (query, doc) pairs with sentence-transformers CrossEncoder."""
        if not self._st_loaded or self._st_model is None:
            raise RuntimeError("sentence-transformers model not loaded")
        texts = [(doc.get("content") or doc.get("text") or "")[:2000] for doc in documents]
        pairs = [(query, text) for text in texts]
        if not pairs:
            return []
        raw = self._st_model.predict(pairs)
        return [float(s) for s in raw]

    # ── public API ───────────────────────────────────────────────

    async def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Rerank *documents* against *query* with a cross-encoder.

        Parameters
        ----------
        query:
            The user query string.
        documents:
            List of candidate document dicts. Each doc must have
            ``id`` and ``content`` (or ``text``); original scores may be
            present under ``score``/``rrf_score``/``similarity``.
        top_k:
            Number of top results to return (clamped to len(documents)).

        Returns
        -------
        list[dict]
            Documents sorted by cross-encoder score descending, augmented
            with ``cross_score``, ``combined_score``, ``rerank_method`` and
            ``rerank_duration_ms`` keys. If no reranker backend is available,
            documents are returned sorted by their original score unchanged.
        """
        if not documents:
            return []

        top_k = min(top_k, len(documents))
        t0 = time.perf_counter()

        def _original(doc: dict[str, Any]) -> float:
            for key in ("score", "rrf_score", "similarity"):
                val = doc.get(key)
                if isinstance(val, (int, float)):
                    return float(val)
            return 0.0

        # ── try Ollama native /api/rerank ──
        if self._use_ollama and await self._ollama_has_reranker():
            try:
                scores = await self._score_ollama(query, documents)
                method = "ollama-bge-reranker-v2-m3"
            except Exception as exc:
                logger.debug("reranker: Ollama /api/rerank failed (%s) — ST fallback", exc)
                scores = []
                method = "sentence-transformers"
        else:
            scores = []
            method = "sentence-transformers"

        # ── try sentence-transformers CrossEncoder ──
        if not scores:
            self._load_st_model()
            if self._st_loaded:
                try:
                    scores = await asyncio.to_thread(self._score_st, query, documents)
                except Exception as exc:
                    logger.warning("reranker: ST scoring failed (%s)", exc)
                    scores = []
            if scores:
                method = "sentence-transformers"

        # ── graceful no-op if no backend worked ──
        if not scores:
            logger.warning("reranker: no backend available — returning documents by original score")
            ordered = sorted(documents, key=_original, reverse=True)[:top_k]
            for doc in ordered:
                doc["rerank_method"] = "none"
            return ordered

        # ── build results ──
        scored: list[tuple[dict[str, Any], float]] = list(zip(documents, scores, strict=True))
        scored.sort(key=lambda x: x[1], reverse=True)

        duration_ms = (time.perf_counter() - t0) * 1000
        results: list[dict[str, Any]] = []
        for doc, cross in scored[:top_k]:
            entry = dict(doc)
            orig = _original(doc)
            entry["cross_score"] = round(cross, 6)
            # Blend: trust the cross-encoder most, keep a hint of the original signal.
            entry["combined_score"] = round(0.7 * cross + 0.3 * orig, 6)
            entry["rerank_method"] = method
            entry["rerank_duration_ms"] = round(duration_ms, 1)
            results.append(entry)

        # ── metrics ──
        if rerank_requests is not None and rerank_latency is not None:
            try:
                rerank_requests.inc()
                rerank_latency.observe(duration_ms / 1000.0)
            except Exception:
                logger.debug("reranker: metrics update failed", exc_info=True)

        logger.info(
            "reranker: %d docs -> top-%d in %.1f ms (backend=%s)",
            len(documents),
            top_k,
            duration_ms,
            method,
        )
        return results

    def health_check(self) -> dict[str, Any]:
        return {
            "backend": "ollama" if self._ollama_available else "sentence-transformers",
            "ollama_model": OLLAMA_RERANK_MODEL,
            "st_model": self._st_model_name,
            "st_loaded": self._st_loaded,
            "st_load_failed": self._st_load_failed,
            "load_time_s": round(self._load_time_s, 2),
            "rss_mb": round(_rss_mb(), 1),
            "max_length": RERANK_MAX_LENGTH,
        }


async def get_reranker() -> Reranker:
    """Async convenience function to obtain the singleton Reranker."""
    return await Reranker.get_reranker()
