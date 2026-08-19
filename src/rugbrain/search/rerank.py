"""
RAG Search — cross-encoder reranking (search lane).

Cross-encoder rerank step for retrieval: after candidate chunks are
retrieved, score each (query, chunk) pair with the self-hosted Qwen3
Reranker 8B (llama.cpp, port 8003) and keep only the most relevant chunks
before truncation.

Backend:
  * Qwen3-Reranker-8B Q4_K_M served by llama.cpp ``--rerank`` on Talos
    (http://172.22.0.1:8003 from inside the container, 127.0.0.1:8003 on
    the host). HTTP-only — no torch, no sentence-transformers, no local
    model weights in the container.
  * Graceful no-op — if the reranker server is unreachable, documents are
    returned ordered by their original retrieval score, tagged
    ``rerank_method="none"``. Search never breaks because the reranker is
    unavailable.

Environment variables:
  - ``RERANKER_URL`` — Qwen3 reranker base URL (default: http://127.0.0.1:8003)
  - ``RERANKER_TIMEOUT`` — per-request timeout in seconds (default: 30)
  - ``RERANKER_ENABLED`` — set "false" to disable reranking entirely
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RERANKER_URL = os.getenv("RERANKER_URL", "http://127.0.0.1:8003").rstrip("/")
RERANKER_TIMEOUT = float(os.getenv("RERANKER_TIMEOUT", "30"))
RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "true").lower() == "true"

# Max content chars fed to the cross-encoder per chunk (truncation guard).
_RERANK_TEXT_LIMIT = 2000

# Cache health so we don't hammer a down server.
_health: dict[str, Any] = {"ok": False, "last_check": 0.0}
_lock = asyncio.Lock()


def _original_score(doc: dict[str, Any]) -> float:
    """Best available retrieval score for a chunk (for candidate selection)."""
    for key in ("score", "rrf_score", "similarity", "bm25_score", "text_score"):
        val = doc.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return 0.0


class Reranker:
    """Backward-compatible search-lane reranker facade.

    Wraps the self-hosted Qwen3-Reranker-8B HTTP endpoint. Kept for
    callers that import ``get_reranker`` from ``rugbrain.search.rerank``;
    new code should call :func:`rerank_chunks` directly.
    """

    def __init__(self, url: str | None = None):
        self._url = url or RERANKER_URL

    async def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Score chunks against query, return top-k with rerank metadata."""
        return await rerank_chunks(query, chunks, top_n=top_k, keep=top_k)

    async def score_candidates(
        self,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> tuple[list[float], str]:
        """Score candidates without truncation (legacy API)."""
        return await _score_candidates(query, candidates)


async def get_reranker() -> Reranker:
    """Return a search-lane reranker instance (backward-compatible)."""
    return Reranker()


def _chunk_text(doc: dict[str, Any]) -> str:
    """Extract searchable text from a chunk dict."""
    for key in ("content", "text", "chunk_text", "title", "summary"):
        val = doc.get(key)
        if isinstance(val, str) and val.strip():
            return val[:_RERANK_TEXT_LIMIT]
    return ""


async def _fast_health() -> bool:
    """Fast cached health check — avoids hammering a down server."""
    async with _lock:
        if _health["ok"] and (time.time() - _health["last_check"]) < 30:
            return True
        if not _health["ok"] and (time.time() - _health["last_check"]) < 60:
            return False
    return True  # Allow the actual request to probe


async def _score_candidates(
    query: str,
    candidates: list[dict[str, Any]],
) -> tuple[list[float], str]:
    """Score candidate chunks against query via the Qwen3 reranker.

    Returns (scores, method). On failure returns ([], "none").
    """
    if not RERANKER_ENABLED or not candidates:
        return [], "none"

    if not await _fast_health():
        return [], "none"

    docs = [_chunk_text(d) for d in candidates]
    # Skip empty docs — llama.cpp would still score them but they add noise.
    valid = [(i, t) for i, t in enumerate(docs) if t]
    if not valid:
        return [], "none"

    try:
        async with httpx.AsyncClient(timeout=RERANKER_TIMEOUT) as client:
            payload = {
                "query": query,
                "documents": [t for _, t in valid],
                "top_n": len(valid),
            }
            resp = await client.post(f"{RERANKER_URL}/rerank", json=payload)
            if resp.status_code != 200:
                logger.warning("reranker_status=%d", resp.status_code)
                return [], "none"

            data = resp.json()
            results = data.get("results", [])

            async with _lock:
                _health["ok"] = True
                _health["last_check"] = time.time()

            # Map back to original candidate indices.
            scores: list[float] = [0.0] * len(candidates)
            for r in results:
                pos = r.get("index")
                if isinstance(pos, int) and 0 <= pos < len(valid):
                    orig_idx = valid[pos][0]
                    scores[orig_idx] = float(r.get("relevance_score", 0.0))
            return scores, "qwen3-reranker-8b"

    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
        logger.debug("reranker_unavailable: %s", exc)
        async with _lock:
            _health["ok"] = False
            _health["last_check"] = time.time()
        return [], "none"


async def rerank_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    top_n: int = 20,
    keep: int = 5,
) -> list[dict[str, Any]]:
    """Re-rank *chunks* against *query* with the Qwen3 cross-encoder, keep the best.

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
        ``rerank_duration_ms``. When the reranker server is unavailable,
        returns the top ``keep`` chunks by their original retrieval score
        with ``rerank_method="none"`` — search behavior is preserved, never
        broken.
    """
    if not chunks:
        return []

    top_n = len(chunks) if top_n <= 0 else min(top_n, len(chunks))
    keep = top_n if keep <= 0 else min(keep, top_n)

    # Candidate selection is by the original retrieval score: cross-encoding
    # is expensive, so only the best top_n candidates are re-scored.
    candidates = sorted(chunks, key=_original_score, reverse=True)[:top_n]

    t0 = time.perf_counter()
    scores, method = await _score_candidates(query, candidates)
    duration_ms = (time.perf_counter() - t0) * 1000

    # ── graceful no-op: reranker unavailable ──
    if not scores or method == "none":
        ordered = sorted(candidates, key=_original_score, reverse=True)[:keep]
        for doc in ordered:
            doc["rerank_method"] = "none"
            doc["reranked"] = False
        return ordered

    scored = sorted(zip(candidates, scores, strict=True), key=lambda x: x[1], reverse=True)

    results: list[dict[str, Any]] = []
    for doc, cross in scored[:keep]:
        entry = dict(doc)
        entry["rerank_score"] = round(float(cross), 6)
        entry["rerank_method"] = method
        entry["reranked"] = True
        entry["rerank_duration_ms"] = round(duration_ms, 1)
        results.append(entry)
    return results
