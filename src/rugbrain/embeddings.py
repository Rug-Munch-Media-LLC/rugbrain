"""M4 RAG Embeddings - strict two-tier (no hash fallback).

Per T04: the hash-based "fallback" embedding was returning
confidently-wrong results (non-semantic vectors). When all neural
backends fail, the RAG system now fails LOUDLY instead of returning
gibberish.

Tier 1: Infinity BAAI/bge-m3 (1024d, self-hosted, zero cost) - strong quality
Tier 2: OpenRouter NVIDIA Nemotron (2048d, free tier) - if API key set

Design (per DESIGN.md M4):
- Single async API: `await get_embedding(text) -> list[float]`
- bge-m3 preferred when available (via Infinity /embeddings)
- Embedding dim is reported via EMBEDDING_DIM; collections use same dim
- All embeddings are float lists (JSON-serializable) for Redis storage
- FAIL LOUD: if both backends fail, raise RAGUnavailableError
- Monitor via Prometheus counter rag_empty_returns_total
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Final

import httpx
import numpy as np

log = logging.getLogger(__name__)

EMBEDDING_DIM: Final = 1024

# Embedding versioning (ADR-0007 compliance)
# Stamped on every embedding so downstream consumers can migrate
# when the model changes. Format: {model}:{version}:{dim}
EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "bge-m3")
EMBED_MODEL_VERSION = os.getenv("RAG_EMBED_VERSION", "1.1")
EMBED_VERSION_STAMP = f"{EMBED_MODEL_NAME}:{EMBED_MODEL_VERSION}:{EMBEDDING_DIM}"
EMBED_BUILD_ID = os.getenv("RAG_EMBED_BUILD_ID", "")  # git commit hash or CI build number
# Backends
_BACKEND = os.getenv("RAG_EMBED_BACKEND", "auto").lower()
INFINITY_URL = os.getenv("INFINITY_URL", "http://infinity-rag:8001").rstrip("/")
INFINITY_MODEL = os.getenv("RAG_INFINITY_EMBED_MODEL", "BAAI/bge-m3")
OPENROUTER_URL = os.getenv("OPENROUTER_EMBED_URL", "https://openrouter.ai/api/v1/embeddings")
OPENROUTER_MODEL = os.getenv("RAG_OPENROUTER_EMBED_MODEL", "openrouter/your-model-here")


class RAGUnavailableError(Exception):
    """Raised when no embedding backend is reachable. The RAG endpoint
    translates this to HTTP 503 + an explicit empty-result body.
    See T04 in MINIMAX_M3_TASKS.md."""


def _infinity_reachable() -> bool:
    """Quick probe: is Infinity listening? Cached per process via env.

    Cache expires after 30s so a cold-start Infinity that was unreachable
    during early startup will be retried automatically. Set
    ``RAG_EMBED_NO_CACHE=1`` to force a fresh probe every time.
    """
    probed = os.getenv("_INFINITY_PROBED")
    probed_at = os.getenv("_INFINITY_PROBED_AT")
    if probed and not os.getenv("RAG_EMBED_NO_CACHE"):
        # Cache is valid for 30s — retry after that in case Infinity was
        # still cold-starting during the initial probe.
        if probed_at and (time.monotonic() - float(probed_at)) < 30.0:
            return os.getenv("_INFINITY_OK") == "1"
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(f"{INFINITY_URL}/health")
            ok = r.status_code == 200
    except Exception:
        ok = False
    os.environ["_INFINITY_PROBED"] = "1"
    os.environ["_INFINITY_PROBED_AT"] = str(time.monotonic())
    os.environ["_INFINITY_OK"] = "1" if ok else "0"
    if not ok:
        log.info("rag_infinity_unreachable url=%s", INFINITY_URL)
    return ok


def _resize(vec: list[float], dim: int) -> list[float]:
    """Pad or truncate a vector to target dim, then L2 normalize."""
    arr = np.asarray(vec, dtype=np.float32)
    arr = (
        arr[:dim]
        if arr.shape[0] >= dim
        else np.concatenate([arr, np.zeros(dim - arr.shape[0], dtype=np.float32)])
    )
    n = float(np.linalg.norm(arr))
    if n > 0:
        arr = arr / n
    return [float(x) for x in arr]


async def _infinity_embed(text: str, dim: int = EMBEDDING_DIM) -> list[float] | None:
    """Try Infinity BAAI/bge-m3 via the OpenAI-compatible /embeddings.

    Returns None on any failure.

    Async (httpx.AsyncClient) so a slow Infinity never blocks the event
    loop — previously the sync client wedged the FastAPI search handler
    for 38-90s, tripping the container healthcheck.
    """
    try:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(
                f"{INFINITY_URL}/embeddings",
                json={"model": INFINITY_MODEL, "input": [text[:8000]]},
            )
            if r.status_code != 200:
                return None
            data = r.json().get("data", [])
            if not data:
                return None
            vec = data[0].get("embedding")
            if not vec or not isinstance(vec, list):
                return None
            return _resize(vec, dim)
    except Exception as e:
        log.debug("infinity_embed_failed: %s", e)
        return None


async def _openrouter_embed(text: str, dim: int = EMBEDDING_DIM) -> list[float] | None:
    """Try OpenRouter Nemotron. Returns None on any failure.

    Async (httpx.AsyncClient) so a slow upstream never blocks the event loop.
    """
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": OPENROUTER_MODEL, "input": text[:8000]},
            )
            if r.status_code != 200:
                return None
            data = r.json().get("data", [])
            if not data:
                return None
            vec = data[0].get("embedding")
            if not vec:
                return None
            return _resize(vec, dim)
    except Exception as e:
        log.debug("openrouter_embed_failed: %s", e)
        return None


async def get_embedding(text: str) -> list[float]:
    """Async embedding API. Returns 1024d float list.

    Per T04: NO hash fallback. If both neural backends fail,
    raises RAGUnavailableError. The RAG endpoint translates this to
    HTTP 503 + {"status": "empty", "chunks": [], "message": ...}.
    """
    if not text or not text.strip():
        raise RAGUnavailableError("empty text input")

    from rugbrain.metrics import embed_latency, embed_requests

    embed_requests.inc()
    t0 = time.perf_counter()
    try:
        return await _get_embedding_impl(text)
    finally:
        embed_latency.observe(time.perf_counter() - t0)


async def _get_embedding_impl(text: str) -> list[float]:
    """Core embedding resolution (separated for latency instrumentation)."""

    # Explicit backend override
    if _BACKEND == "infinity":
        v = await _infinity_embed(text)
        if v is not None:
            return v
        raise RAGUnavailableError("infinity backend failed and hash fallback removed per T04")
    if _BACKEND == "openrouter":
        v = await _openrouter_embed(text)
        if v is not None:
            return v
        raise RAGUnavailableError("openrouter backend failed and hash fallback removed per T04")

    # auto: try infinity -> openrouter, fail loud if both fail
    # Probe is sync (cached per process) — run off the event loop so a slow
    # first probe can never wedge the request handler.
    if await asyncio.to_thread(_infinity_reachable):
        v = await _infinity_embed(text)
        if v is not None:
            return v
    v = await _openrouter_embed(text)
    if v is not None:
        return v

    raise RAGUnavailableError(
        "no embedding backend available (infinity unreachable, openrouter failed). "
        "Per T04, hash fallback removed to prevent false-confidence in RAG results. "
        f"Verify: INFINITY_URL={INFINITY_URL} BAAI/bge-m3 loaded?"
    )


async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Batch embedding. Sequential calls - Infinity and OpenRouter handle
    larger requests poorly and we'd rather keep memory bounded."""
    return [await get_embedding(t) for t in texts]


def current_backend() -> str:
    """Which tier is currently active? For observability."""
    if _BACKEND != "auto":
        return _BACKEND
    if _infinity_reachable():
        return "infinity"
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    return "unavailable"
