"""
RAG Search — cross-encoder reranking (search lane).

Cross-encoder rerank step for retrieval: after candidate chunks are
retrieved, score each (query, chunk) pair with the self-hosted Qwen3
Reranker 4B (llama.cpp, port 8003) and keep only the most relevant chunks
before truncation.

Backend:
  * Qwen3-Reranker-4B Q6_K served by llama.cpp ``--rerank`` on Talos
    (http://127.0.0.1:8003 from inside the container, 127.0.0.1:8003 on
    the host). HTTP-only — no torch, no sentence-transformers, no local
    model weights in the container.
  * Graceful no-op — if the reranker server is unreachable, documents are
    returned ordered by their original retrieval score, tagged
    ``rerank_method="none"``. Search never breaks because the reranker is
    unavailable.

This module owns the two mutable ledgers (the endpoint strain ledger and the
health cache) because tests mutate them by name on this module; they cannot
be re-exported by value. The instruction library and the score-scattering
helpers live in sibling modules.

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
from collections import defaultdict
from typing import Any, cast

import httpx

from rugbrain.rerank_hosted import hosted_enabled, hosted_rerank
from rugbrain.search.rerank_instructions import apply_instruction, detect_vertical
from rugbrain.search.rerank_scores import (
    RERANK_TEXT_LIMIT,
    TOKEN_BUDGET_CAP,
    chunk_text,
    original_score,
    scatter,
    valid_docs,
)

logger = logging.getLogger(__name__)

# Private aliases kept for the tests (and any legacy caller) that reach for
# these by name on this module. Mutable state is NOT re-exported this way —
# the strain ledger and health cache below are the single copy.
_RERANK_TEXT_LIMIT = RERANK_TEXT_LIMIT
_TOKEN_BUDGET_CAP = TOKEN_BUDGET_CAP
_chunk_text = chunk_text

RERANKER_URL = os.getenv("RERANKER_URL", "http://127.0.0.1:8003").rstrip("/")
RERANKER_TIMEOUT = float(os.getenv("RERANKER_TIMEOUT", "30"))
RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "true").lower() == "true"
# Live-lane candidate cap + the local 4B fallback cap. The 0.6B speed
# tier these used to bound was retired 2026-08-29; its endpoint was removed
# 2026-09-14 (all rerank traffic is the 4B family now).
LIVE_CANDIDATE_CAP: int = int(os.getenv("RERANKER_LIVE_CANDIDATE_CAP", "40"))
DEEP_FALLBACK_CAP: int = int(os.getenv("RERANKER_DEEP_FALLBACK_CAP", "24"))
# Hosted (DeepInfra) live rung — replacement for the retired 0.6B speed tier
# as the interactive lane (2026-09-14; same 4B cross-encoder family, ~0.5-2s
# for a full pool vs 30s+ local on 2000-char docs).
HOSTED_TIMEOUT_S: float = float(os.getenv("RERANKER_HOSTED_TIMEOUT", "6.0"))
HOSTED_CANDIDATE_CAP: int = int(os.getenv("RERANKER_HOSTED_CAP", "40"))
# The deep tier used to score EVERY fused candidate. A code-intent query fans
# out over 8 collections x limit=10 -> 80 candidates -> measured 17,488
# tokens to the cross-encoder for one query (35,976 at limit=20), while the
# live tier capped at 40 the whole time. This bounds the deep tier the same way.
DEEP_CANDIDATE_CAP: int = int(os.getenv("RERANKER_DEEP_CANDIDATE_CAP", "40"))
# Sigmoid P(relevant) below this is confidently-irrelevant noise (Qwen3
# rerank scores are 0-1). Applied only when enough strong candidates remain.
_RERANK_NOISE_FLOOR = 0.10

__all__ = [
    "DEEP_CANDIDATE_CAP",
    "RERANKER_ENABLED",
    "RERANKER_TIMEOUT",
    "RERANKER_URL",
    "_RERANK_TEXT_LIMIT",
    "_TOKEN_BUDGET_CAP",
    "Reranker",
    "_chunk_text",
    "apply_instruction",
    "detect_vertical",
    "get_reranker",
    "original_score",
    "rerank_chunks",
]

# ── Endpoint strain ledger (mutable module state — stays here) ──────────
# A failing endpoint is skipped for _STRAIN_WINDOW_S after _STRAIN_THRESHOLD
# recent failures, so a down server costs one probe per window per URL
# instead of one per query.
_STRAIN_WINDOW_S: float = 120.0
_STRAIN_THRESHOLD: int = 3
_strain_failures: dict[str, list[float]] = defaultdict(list)

# Cache health so we don't hammer a down server.
_health: dict[str, Any] = {"ok": False, "last_check": 0.0}
_lock = asyncio.Lock()


def _is_strained(url: str) -> bool:
    """True when this endpoint recently failed repeatedly — skip it."""
    now = time.time()
    recent = [t for t in _strain_failures.get(url, []) if now - t < _STRAIN_WINDOW_S]
    _strain_failures[url] = recent
    return len(recent) >= _STRAIN_THRESHOLD


def _record_strain(url: str) -> None:
    _strain_failures[url].append(time.time())


def _clear_strain(url: str | None = None) -> None:
    if url is None:
        _strain_failures.clear()
    else:
        _strain_failures.pop(url, None)


async def _fast_health() -> bool:
    """Fast cached health check — avoids hammering a down server."""
    async with _lock:
        if _health["ok"] and (time.time() - _health["last_check"]) < 30:
            return True
        if not _health["ok"] and (time.time() - _health["last_check"]) < 60:
            return False
    return True  # Allow the actual request to probe


class Reranker:
    """Backward-compatible search-lane reranker facade.

    Wraps the self-hosted Qwen3-Reranker-4B HTTP endpoint. Kept for
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


async def _post_rerank(
    url: str,
    query: str,
    docs: list[str],
    timeout: float,  # noqa: ASYNC109 — per-endpoint policy value
    model_label: str,
) -> list[dict[str, Any]] | None:
    """Single POST to a reranker endpoint. None on failure (strain recorded)."""
    if _is_strained(url):
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{url}/rerank",
                json={
                    "query": apply_instruction(query),
                    "documents": docs,
                    "top_n": len(docs),
                },
            )
            if resp.status_code != 200:
                logger.warning("reranker_status=%d url=%s", resp.status_code, url[:40])
                _record_strain(url)
                return None
            data = resp.json()
            results = data.get("results", [])
            _clear_strain(url)
            return cast("list[dict[str, Any]] | None", results)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
        logger.debug("reranker_unavailable url=%s: %s", url[:40], exc)
        _record_strain(url)
        return None


async def _hosted_scores(
    query: str,
    valid: list[tuple[int, str]],
    total: int,
) -> tuple[list[float], str] | None:
    """Hosted DeepInfra rung, candidate-capped. None when unavailable."""
    if not hosted_enabled():
        return None
    capped = valid[:HOSTED_CANDIDATE_CAP]
    results = await hosted_rerank(
        query, [t for _, t in capped], top_n=len(capped), timeout=HOSTED_TIMEOUT_S
    )
    if results is None:
        return None
    return scatter(results, capped, total), "deepinfra:qwen3-reranker-4b"


async def _score_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    tier: str = "deep",
) -> tuple[list[float], str]:
    """Score candidate chunks against query via the Qwen3 reranker tiers.

    tier="live": hosted lane first, then the local 4B (candidate-capped);
    tier="deep" (default): local 4B first, hosted fallback. Returns
    (scores, method).
    """
    if not RERANKER_ENABLED or not candidates:
        return [], "none"

    valid = valid_docs(candidates)
    if not valid:
        return [], "none"

    if tier == "live":
        # 0th rung: hosted DeepInfra (same 4B family, ~0.5-2s per pool; the
        # retired 0.6B speed tier's replacement as the interactive lane).
        hosted = await _hosted_scores(query, valid, len(candidates))
        if hosted is not None:
            return hosted
        # 1st rung (the only local lane): the 4B, candidate-capped. The
        # 0.6B speed tier that used to sit here is retired (endpoint gone).
        capped = valid[:LIVE_CANDIDATE_CAP][:DEEP_FALLBACK_CAP]
        results = await _post_rerank(
            RERANKER_URL, query, [t for _, t in capped], RERANKER_TIMEOUT, "4B"
        )
        if results is None:
            return [], "none"
        return scatter(results, capped, len(candidates)), "qwen3-reranker-4b"

    # ── deep tier (default): local 4B first (free), hosted fallback ──
    # Bounded: the deep tier used to score every fused candidate uncapped,
    # while the live tier was capped at 40. A code-intent query produced 80
    # candidates (17,488 measured tokens); this makes the two rungs agree.
    deep_valid = valid[:DEEP_CANDIDATE_CAP]
    if await _fast_health():
        results = await _post_rerank(
            RERANKER_URL, query, [t for _, t in deep_valid], RERANKER_TIMEOUT, "4B"
        )
        if results is not None:
            async with _lock:
                _health["ok"] = True
                _health["last_check"] = time.time()
            return scatter(results, deep_valid, len(candidates)), "qwen3-reranker-4b"
        async with _lock:
            _health["ok"] = False
            _health["last_check"] = time.time()

    # Local unavailable / known-down: hosted keeps deep-tier rerank alive.
    hosted = await _hosted_scores(query, valid, len(candidates))
    if hosted is not None:
        return hosted
    return [], "none"


async def rerank_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    top_n: int = 20,
    keep: int = 5,
    tier: str = "deep",
) -> list[dict[str, Any]]:
    """Re-rank `chunks` against `query`, keeping the best `keep`.

    A thin delegate. The pipeline lives in `rerank_pipeline`; the scorer is
    passed IN rather than imported, so `monkeypatch.setattr(rerank,
    "_post_rerank", ...)` and `rerank.DEEP_CANDIDATE_CAP = 5` still take effect
    through this module's namespace. Importing the scorer here instead would
    have frozen a copy at import time — the same "resolved once, then silently
    stale" class of bug this refactor is trying to remove.
    """
    from rugbrain.search.rerank_pipeline import rerank_chunks as _pipeline

    return await _pipeline(
        query, chunks, _score_candidates, top_n=top_n, keep=keep, tier=tier
    )
