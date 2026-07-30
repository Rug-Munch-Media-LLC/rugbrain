"""M4 RAG Embeddings - strict two-tier (no hash fallback).

Per T04: the hash-based "fallback" embedding was returning
confidently-wrong results (non-semantic vectors). When all neural
backends fail, the RAG system now fails LOUDLY instead of returning
gibberish.

Tier 1: Ollama qwen3-embed:4b (2560d, our own, zero cost) - SOTA quality
Tier 2: OpenRouter NVIDIA Nemotron (2048d, free tier) - if API key set

Design (per DESIGN.md M4):
- Single async API: `await get_embedding(text) -> list[float]`
- Qwen3-embed preferred when available
- Embedding dim is reported via EMBEDDING_DIM; collections use same dim
- All embeddings are float lists (JSON-serializable) for Redis storage
- FAIL LOUD: if both backends fail, raise RAGUnavailableError
- Monitor via Prometheus counter rag_empty_returns_total
"""

from __future__ import annotations

import logging
import os
from typing import Final

import httpx
import numpy as np

log = logging.getLogger(__name__)

EMBEDDING_DIM: Final = 2560

# Embedding versioning (ADR-0007 compliance)
# Stamped on every embedding so downstream consumers can migrate
# when the model changes. Format: {model}:{version}:{dim}
EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embed:4b")
EMBED_MODEL_VERSION = os.getenv("RAG_EMBED_VERSION", "1.1")
EMBED_VERSION_STAMP = f"{EMBED_MODEL_NAME}:{EMBED_MODEL_VERSION}:{EMBEDDING_DIM}"
EMBED_BUILD_ID = os.getenv("RAG_EMBED_BUILD_ID", "")  # git commit hash or CI build number
# Backends
_BACKEND = os.getenv("RAG_EMBED_BACKEND", "auto").lower()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "qwen3-embed:4b")
OPENROUTER_URL = os.getenv("OPENROUTER_EMBED_URL", "https://openrouter.ai/api/v1/embeddings")
OPENROUTER_MODEL = os.getenv("RAG_OPENROUTER_EMBED_MODEL", "openrouter/your-model-here")


class RAGUnavailableError(Exception):
    """Raised when no embedding backend is reachable. The RAG endpoint
    translates this to HTTP 503 + an explicit empty-result body.
    See T04 in MINIMAX_M3_TASKS.md."""


def _ollama_reachable() -> bool:
    """Quick probe: is Ollama listening? Cached per process via env."""
    if os.getenv("_OLLAMA_PROBED"):
        return os.getenv("_OLLAMA_OK") == "1"
    try:
        with httpx.Client(timeout=2.0) as c:
            r = c.get(f"{OLLAMA_URL}/api/tags")
            ok = r.status_code == 200
    except Exception:
        ok = False
    os.environ["_OLLAMA_PROBED"] = "1"
    os.environ["_OLLAMA_OK"] = "1" if ok else "0"
    if not ok:
        log.info("rag_ollama_unreachable url=%s", OLLAMA_URL)
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
    return arr.tolist()


def _ollama_embed(text: str, dim: int = EMBEDDING_DIM) -> list[float] | None:
    """Try Ollama qwen3-embed:4b. Returns None on any failure."""
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": OLLAMA_MODEL, "prompt": text[:8000]},
            )
            if r.status_code != 200:
                return None
            vec = r.json().get("embedding")
            if not vec or not isinstance(vec, list):
                return None
            return _resize(vec, dim)
    except Exception as e:
        log.debug("ollama_embed_failed: %s", e)
        return None


def _openrouter_embed(text: str, dim: int = EMBEDDING_DIM) -> list[float] | None:
    """Try OpenRouter Nemotron. Returns None on any failure."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        with httpx.Client(timeout=15.0) as c:
            r = c.post(
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
    """Async embedding API. Returns 2560d float list.

    Per T04: NO hash fallback. If both neural backends fail,
    raises RAGUnavailableError. The RAG endpoint translates this to
    HTTP 503 + {"status": "empty", "chunks": [], "message": ...}.
    """
    if not text or not text.strip():
        raise RAGUnavailableError("empty text input")

    # Explicit backend override
    if _BACKEND == "ollama":
        v = _ollama_embed(text)
        if v is not None:
            return v
        raise RAGUnavailableError("ollama backend failed and hash fallback removed per T04")
    if _BACKEND == "openrouter":
        v = _openrouter_embed(text)
        if v is not None:
            return v
        raise RAGUnavailableError("openrouter backend failed and hash fallback removed per T04")

    # auto: try ollama -> openrouter, fail loud if both fail
    if _ollama_reachable():
        v = _ollama_embed(text)
        if v is not None:
            return v
    v = _openrouter_embed(text)
    if v is not None:
        return v
        raise RAGUnavailableError(
            "no embedding backend available (ollama unreachable, openrouter failed). "
            "Per T04, hash fallback removed to prevent false-confidence in RAG results. "
            f"Verify: OLLAMA_URL={OLLAMA_URL} qwen3-embed:4b pulled?"
        )


async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Batch embedding. Sequential calls - Ollama and OpenRouter handle
    larger requests poorly and we'd rather keep memory bounded."""
    return [await get_embedding(t) for t in texts]


def current_backend() -> str:
    """Which tier is currently active? For observability."""
    if _BACKEND != "auto":
        return _BACKEND
    if _ollama_reachable():
        return "ollama"
    if os.getenv("OPENROUTER_API_KEY"):
        return "openrouter"
    return "unavailable"
