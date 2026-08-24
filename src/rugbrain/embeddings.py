"""M4 RAG Embeddings - strict two-tier (no hash fallback).

Per T04: the hash-based "fallback" embedding was returning
confidently-wrong results (non-semantic vectors). When all neural
backends fail, the RAG system now fails LOUDLY instead of returning
gibberish.

Tier 1: Qwen3-Embedding-4B (2560d, self-hosted, zero cost) - strong quality
Tier 2: OpenRouter NVIDIA Nemotron (2048d, free tier) - if API key set

Design (per DESIGN.md M4):
- Single async API: `await get_embedding(text) -> list[float]`
- qwen3-embedding-4b preferred (via llama.cpp /embeddings)
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

# Migration window (2026-08-24): the interim free embedder
# (nvidia/nemotron-3-embed-1b:free) produces 2048-dim vectors. Set
# RAG_EMBED_DIM=2048 alongside RAG_EMBED_BACKEND=openrouter +
# RAG_INTERIM_COLLECTION_SUFFIX so embed-time validation matches the live
# backend and the shadow collections it feeds. Remove all three once
# rag-embed-migration.service completes and the local qwen3-embedding-4b
# (native 2560) resumes — the default below is the canonical dim.
EMBEDDING_DIM: Final = int(os.getenv("RAG_EMBED_DIM", "2560"))

# Qwen3-Embedding query instruction template. The model was trained with an
# instruction prefix on the QUERY side and plain text on the DOCUMENT side.
# Embedding a raw query without the Instruct template measurably degrades
# retrieval (roughly 1-5% on nDCG/recall for instruction-tuned embedding
# models). Documents MUST stay raw; only query text gets this prefix.
QUERY_INSTRUCT = (
    "Instruct: Given a web search query, retrieve relevant passages that "
    "answer the query\nQuery: {}"
)

# Embedding versioning (ADR-0007 compliance)
# Stamped on every embedding so downstream consumers can migrate
# when the model changes. Format: {model}:{version}:{dim}
EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")
EMBED_MODEL_VERSION = os.getenv("RAG_EMBED_VERSION", "2.0")
EMBED_VERSION_STAMP = f"{EMBED_MODEL_NAME}:{EMBED_MODEL_VERSION}:{EMBEDDING_DIM}"
EMBED_BUILD_ID = os.getenv("RAG_EMBED_BUILD_ID", "")  # git commit hash or CI build number
# Backends
_BACKEND = os.getenv("RAG_EMBED_BACKEND", "auto").lower()
# Qwen3-Embedding-4B served by llama.cpp (OpenAI-compatible /embeddings).
# QWEN_EMBED_URL is the canonical embedder endpoint (llama.cpp Qwen3-4B).
QWEN_EMBED_URL = os.getenv(
    "QWEN_EMBED_URL", os.getenv("QWEN_EMBED_URL", "http://172.22.0.1:8004/v1")
).rstrip("/")
GATEWAY_EMBED_URL = os.getenv("GATEWAY_EMBED_URL", "http://172.22.0.1:8010")
QWEN_EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b"))
OPENROUTER_URL = os.getenv("OPENROUTER_EMBED_URL", "https://openrouter.ai/api/v1/embeddings")
OPENROUTER_MODEL = os.getenv("RAG_OPENROUTER_EMBED_MODEL", "openrouter/your-model-here")


class RAGUnavailableError(Exception):
    """Raised when no embedding backend is reachable. The RAG endpoint
    translates this to HTTP 503 + an explicit empty-result body.
    See T04 in MINIMAX_M3_TASKS.md."""


def _int_reachable() -> bool:
    """Quick probe: is Qwen3-4B listening? Cached per process via env.

    Cache expires after 30s so a cold-start llama.cpp that was unreachable
    during early startup will be retried automatically. Set
    ``RAG_EMBED_NO_CACHE=1`` to force a fresh probe every time.
    """
    probed = os.getenv("_QWEN_PROBED")
    probed_at = os.getenv("_QWEN_PROBED_AT")
    if (
        probed
        and not os.getenv("RAG_EMBED_NO_CACHE")
        # Cache is valid for 30s — retry after that in case llama.cpp was
        # still cold-starting during the initial probe.
        and probed_at
        and (time.monotonic() - float(probed_at)) < 30.0
    ):
        return os.getenv("_QWEN_OK") == "1"
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(f"{QWEN_EMBED_URL}/health")
            ok = r.status_code == 200
    except Exception:
        ok = False
    os.environ["_QWEN_PROBED"] = "1"
    os.environ["_QWEN_PROBED_AT"] = str(time.monotonic())
    os.environ["_QWEN_OK"] = "1" if ok else "0"
    if not ok:
        log.info("rag_int_unreachable url=%s", QWEN_EMBED_URL)
    return ok


def _resize(vec: list[float], dim: int) -> list[float]:
    """L2-normalize a vector; REJECT dim mismatches (TOP-100 #25).

    Silent zero-pad/truncate masked provider contract drift (a 1024-dim vec
    silently padded into a 2560-dim index poisons retrieval). Fail loud.
    """
    if len(vec) != dim:
        log.error("embedding_dim_mismatch got=%d want=%d", len(vec), dim)
        raise ValueError(f"embedding dim mismatch: got {len(vec)}, expected {dim}")
    arr = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(arr))
    if n > 0:
        arr = arr / n
    return [float(x) for x in arr]


async def _int_embed(
    text: str,
    dim: int = EMBEDDING_DIM,
    query: bool = False,
    timeout: float = 300.0,  # noqa: ASYNC109 - per-call policy: queries fail fast
) -> list[float] | None:
    """Try Qwen3-Embedding-4B via the OpenAI-compatible /embeddings.

    Returns None on any failure.

    Async (httpx.AsyncClient) so a slow llama.cpp never blocks the event
    loop — previously the sync client wedged the FastAPI search handler
    for 38-90s, tripping the container healthcheck.
    """
    try:
        payload_text = QUERY_INSTRUCT.format(text) if query else text
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(
                f"{QWEN_EMBED_URL}/embeddings",
                json={"model": QWEN_EMBED_MODEL, "input": [payload_text[:8000]]},
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
        log.debug("int_embed_failed: %s", e)
        return None


async def _gateway_embed(
    text: str, dim: int = EMBEDDING_DIM, query: bool = False
) -> list[float] | None:
    """Model Services Gateway :8010 lane (breaker + cloud fallback inside).

    Tier is chosen by DIM COMPATIBILITY with the target index: 2560 -> deep
    lane, anything else -> speed lane. Cloud-served responses are REFUSED for
    indexing (different model space poisons retrieval) — logged and None'd so
    the caller falls through / fails loud per T04.
    """
    tier = "deep" if dim >= 2048 else "speed"
    try:
        payload_text = QUERY_INSTRUCT.format(text) if query else text
        async with httpx.AsyncClient(timeout=75.0) as c:
            r = await c.post(
                f"{GATEWAY_EMBED_URL}/embed",
                json={"text": payload_text[:8000], "tier": tier},
            )
            if r.status_code != 200:
                return None
            d = r.json()
        vecs = d.get("embeddings") or []
        vec = vecs[0] if vecs else None
        if not vec:
            return None
        if d.get("served_by") == "cloud":
            log.warning(
                "gateway_served_cloud tier=%s model=%s dims=%s — refused for "
                "index use (cross-model space)",
                tier,
                d.get("cloud_model"),
                d.get("model_dims"),
            )
            return None
        return _resize(vec, dim)
    except Exception as e:
        log.debug("gateway_embed_failed: %s", e)
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


async def _get_embedding_impl(text: str, query: bool = False) -> list[float]:
    """Core embedding resolution (separated for latency instrumentation)."""

    # Explicit backend override
    if _BACKEND == "int":
        v = await _int_embed(text, query=query)
        if v is not None:
            return v
        raise RAGUnavailableError("int backend failed and hash fallback removed per T04")
    if _BACKEND == "openrouter":
        v = await _openrouter_embed(text)
        if v is not None:
            return v
        raise RAGUnavailableError("openrouter backend failed and hash fallback removed per T04")

    # auto: try int -> openrouter, fail loud if both fail
    # Probe is sync (cached per process) — run off the event loop so a slow
    # first probe can never wedge the request handler.
    q_timeout = 45.0 if query else 300.0
    if await asyncio.to_thread(_int_reachable):
        v = await _int_embed(text, query=query, timeout=q_timeout)
        if v is not None:
            return v
    v = await _gateway_embed(text, query=query)
    if v is not None:
        return v
    v = await _openrouter_embed(text)
    if v is not None:
        return v

    raise RAGUnavailableError(
        "no embedding backend available (int unreachable, openrouter failed). "
        "Per T04, hash fallback removed to prevent false-confidence in RAG results. "
        f"Verify: QWEN_EMBED_URL={QWEN_EMBED_URL} qwen3-embedding-4b loaded?"
    )


async def get_embedding(text: str, query: bool = False) -> list[float]:
    """Async embedding API. Returns 2560d float list.

    Per T04: NO hash fallback. If both neural backends fail,
    raises RAGUnavailableError. The RAG endpoint translates this to
    HTTP 503 + {"status": "empty", "chunks": [], "message": ...}.

    Set ``query=True`` for QUERY-side text — prepends the Qwen3
    ``Instruct:`` template the model was trained with (documents stay raw).
    """
    if not text or not text.strip():
        raise RAGUnavailableError("empty text input")

    from rugbrain.metrics import embed_latency, embed_requests

    embed_requests.inc()
    t0 = time.perf_counter()
    try:
        return await _get_embedding_impl(text, query=query)
    finally:
        embed_latency.observe(time.perf_counter() - t0)


async def get_query_embedding(query: str) -> list[float]:
    """Embed a SEARCH QUERY with the Qwen3 ``Instruct:`` query template.

    Use everywhere a user query is embedded for retrieval. Documents going
    into collections must NOT use this — feed them raw via ``get_embedding``.
    """
    return await get_embedding(query, query=True)


async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Batch embedding. Sequential calls - llama.cpp and OpenRouter handle
    larger requests poorly and we'd rather keep memory bounded."""
    return [await get_embedding(t) for t in texts]


def current_backend() -> str:
    """Which tier is currently active? For observability."""
    if _BACKEND != "auto":
        return _BACKEND
    if _int_reachable():
        return "int"
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    return "unavailable"
