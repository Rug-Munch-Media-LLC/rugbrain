"""
RAG Search — cross-encoder reranking (search lane).

Cross-encoder rerank step for retrieval: after candidate chunks are
retrieved, score each (query, chunk) pair with a BGE reranker and keep only
the most relevant chunks before truncation.

Unlike ``rugbrain.reranker`` (the news-lane reranker, Infinity-first with
rating boosts), this module is a self-contained search-lane reranker:

  * Backend 1 — sentence-transformers ``CrossEncoder`` with a BGE reranker
    model (default ``BAAI/bge-reranker-v2-m3``).
  * Backend 2 — ``FlagEmbedding.FlagReranker`` with the same model id.
  * Graceful no-op — if neither backend can be loaded (the model / torch is
    not installed in this environment), documents are returned ordered by
    their original retrieval score, tagged ``rerank_method="none"``. Search
    never breaks because the reranker is unavailable.

All heavy imports (torch, sentence_transformers, FlagEmbedding) are lazy —
``import rugbrain.search.rerank`` always succeeds, even in minimal
environments. The model is loaded once per process (singleton) and kept
resident; loading and scoring run in worker threads so the event loop is
never blocked.

Environment variables:
  - ``RERANK_MODEL_NAME`` — HuggingFace model id (default: BAAI/bge-reranker-v2-m3)
  - ``RERANK_MAX_LENGTH`` — max tokens per (query, chunk) pair (default: 512)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# BGE multilingual reranker: scores (query, chunk) pairs for crypto + code
# content. Overridable via env for deployments that pin a different model.
RERANK_MODEL_NAME = os.getenv("RERANK_MODEL_NAME", "BAAI/bge-reranker-v2-m3")
RERANK_MAX_LENGTH = int(os.getenv("RERANK_MAX_LENGTH", "512"))

# Max content chars fed to the cross-encoder per chunk (truncation guard).
_RERANK_TEXT_LIMIT = 2000


class Reranker:
    """Lazy cross-encoder reranker with a graceful no-op fallback.

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

    def __init__(self, model_name: str = RERANK_MODEL_NAME) -> None:
        self._model_name = model_name
        self._st_model: Any | None = None
        self._st_ready = False
        self._st_failed = False
        self._flag_model: Any | None = None
        self._flag_ready = False
        self._flag_failed = False
        self._backend_name: str | None = None
        self._load_time_s = 0.0

    # ── backend discovery / loading ───────────────────────────────

    def _load_st(self) -> bool:
        """Load the sentence-transformers CrossEncoder (BGE reranker)."""
        try:
            # Force full torch initialization BEFORE sentence-transformers is
            # imported (bge-reranker-v2-m3 pulls in torch; see rugbrain.reranker
            # for the "partially initialized module 'torch'" failure mode).
            import torch

            torch.classes  # noqa: B018 - trigger full torch init

            from sentence_transformers import CrossEncoder

            self._st_model = CrossEncoder(self._model_name, max_length=RERANK_MAX_LENGTH)
            self._st_ready = True
            return True
        except Exception as exc:
            self._st_failed = True
            logger.warning("search reranker: sentence-transformers backend unavailable (%s)", exc)
            return False

    def _load_flag(self) -> bool:
        """Load the FlagEmbedding FlagReranker as a second backend option."""
        try:
            from FlagEmbedding import FlagReranker

            self._flag_model = FlagReranker(self._model_name)
            self._flag_ready = True
            return True
        except Exception as exc:
            self._flag_failed = True
            logger.warning("search reranker: FlagEmbedding backend unavailable (%s)", exc)
            return False

    def _ensure_backend(self) -> str | None:
        """Load the first available backend; returns its name or ``None``.

        Cached per process: after a successful load the backend is never
        re-probed; after both backends fail we never retry the import. A
        backend that is already ready (e.g. injected in tests) is reused.
        """
        if self._backend_name is not None:
            return self._backend_name
        if self._st_ready:
            self._backend_name = "sentence-transformers"
            return self._backend_name
        if self._flag_ready:
            self._backend_name = "flag-embedding"
            return self._backend_name
        t0 = time.perf_counter()
        if not self._st_failed and self._load_st():
            self._backend_name = "sentence-transformers"
        elif not self._flag_failed and self._load_flag():
            self._backend_name = "flag-embedding"
        if self._backend_name is not None:
            self._load_time_s = time.perf_counter() - t0
            logger.info(
                "search reranker: loaded %s (backend=%s, %.1fs)",
                self._model_name,
                self._backend_name,
                self._load_time_s,
            )
        else:
            logger.warning("search reranker: no backend available — reranking disabled")
        return self._backend_name

    def _score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Score (query, chunk) pairs with whichever backend is loaded.

        Returns an empty list when no backend is ready.
        """
        if self._st_ready and self._st_model is not None:
            raw = self._st_model.predict(pairs)
            return [float(s) for s in raw]
        if self._flag_ready and self._flag_model is not None:
            raw = self._flag_model.compute_score(pairs)
            if isinstance(raw, (int, float)):
                return [float(raw)]
            return [float(s) for s in raw]
        return []

    # ── scoring API ───────────────────────────────────────────────

    async def score_candidates(
        self, query: str, documents: list[dict[str, Any]]
    ) -> tuple[list[float], str]:
        """Cross-encode ``(query, doc)`` pairs for every document.

        Returns ``(scores, backend_name)`` with ``scores`` aligned to
        ``documents`` order. When no backend is available returns
        ``([], "none")`` — callers fall back to the original ordering.
        """
        if not documents:
            return [], "none"
        backend_name = self._backend_name
        if backend_name is None:
            backend_name = await asyncio.to_thread(self._ensure_backend)
        if backend_name is None:
            return [], "none"

        texts = [
            (doc.get("content") or doc.get("text") or "")[:_RERANK_TEXT_LIMIT] for doc in documents
        ]
        pairs = [(query, text) for text in texts]
        try:
            scores = await asyncio.to_thread(self._score_pairs, pairs)
        except Exception as exc:
            logger.warning("search reranker: scoring failed (%s)", exc)
            return [], "none"
        if not scores:
            return [], "none"
        return scores, backend_name

    def health_check(self) -> dict[str, Any]:
        return {
            "backend": self._backend_name or "none",
            "model": self._model_name,
            "st_ready": self._st_ready,
            "flag_ready": self._flag_ready,
            "load_time_s": round(self._load_time_s, 2),
            "max_length": RERANK_MAX_LENGTH,
        }


async def get_reranker() -> Reranker:
    """Async convenience function to obtain the singleton Reranker."""
    return await Reranker.get_reranker()


def _original_score(doc: dict[str, Any]) -> float:
    """Best available retrieval score for a chunk (for candidate selection)."""
    for key in ("score", "rrf_score", "similarity", "bm25_score", "text_score"):
        val = doc.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0


async def rerank_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    top_n: int = 20,
    keep: int = 5,
) -> list[dict[str, Any]]:
    """Re-rank *chunks* against *query* with a cross-encoder, keep the best.

    Parameters
    ----------
    query:
        The user query string.
    chunks:
        Candidate chunk dicts (each with ``id``/``doc_id`` and ``content`` or
        ``text``; retrieval scores under ``score``/``rrf_score``/``similarity``).
    top_n:
        Number of top candidates (by original retrieval score) to re-rank.
        ``<= 0`` means "score all chunks". Default 20.
    keep:
        Number of best re-ranked chunks to return. ``<= 0`` means "keep all
        scored". Default 5. Clamped to ``top_n``.

    Returns
    -------
    list[dict]
        Chunks sorted by cross-encoder relevance descending, augmented with
        ``rerank_score``, ``rerank_method``, ``reranked`` and
        ``rerank_duration_ms``. When no cross-encoder backend is available
        (model not installed), returns the top ``keep`` chunks by their
        original retrieval score with ``rerank_method="none"`` — search
        behavior is preserved, never broken.
    """
    if not chunks:
        return []

    top_n = len(chunks) if top_n <= 0 else min(top_n, len(chunks))
    keep = top_n if keep <= 0 else min(keep, top_n)

    # Candidate selection is by the original retrieval score: cross-encoding
    # is expensive, so only the best top_n candidates are re-scored.
    candidates = sorted(chunks, key=_original_score, reverse=True)[:top_n]

    reranker = await get_reranker()
    scores, method = await reranker.score_candidates(query, candidates)

    # ── graceful no-op: no model available ──
    if not scores:
        ordered = sorted(candidates, key=_original_score, reverse=True)[:keep]
        for doc in ordered:
            doc["rerank_method"] = "none"
            doc["reranked"] = False
        return ordered

    t0 = time.perf_counter()
    scored = sorted(zip(candidates, scores, strict=True), key=lambda x: x[1], reverse=True)
    duration_ms = (time.perf_counter() - t0) * 1000

    results: list[dict[str, Any]] = []
    for doc, cross in scored[:keep]:
        entry = dict(doc)
        entry["rerank_score"] = round(float(cross), 6)
        entry["rerank_method"] = method
        entry["reranked"] = True
        entry["rerank_duration_ms"] = round(duration_ms, 1)
        results.append(entry)
    return results
