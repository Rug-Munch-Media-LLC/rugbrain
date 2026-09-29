"""Local llama.cpp lane — Qwen3-Embedding-8B served at ``:8004``.

Same model as the DeepInfra primary ⇒ one 4096 vector space, so falling back
between the two never breaks index compatibility.

Async throughout: a sync client wedged the FastAPI search handler for 38-90s
and tripped the container healthcheck, so the probe is cached and moved off
the event loop, and the embed call is ``httpx.AsyncClient``.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from rugbrain.embeddings_pkg.config import (
    EMBEDDING_DIM,
    QUERY_INSTRUCT,
    QWEN_EMBED_MODEL,
    QWEN_EMBED_URL,
)
from rugbrain.embeddings_pkg.normalize import resize

log = logging.getLogger(__name__)

#: Embed text is clipped to this many CHARS before the provider sees it
#: (2048-token budget; see ``rugbrain.token_budget``).
MAX_CHARS: int = 8000

#: Per-call timeouts. Queries fail fast; an ingest of long documents may wait.
DOC_TIMEOUT: float = 300.0
QUERY_TIMEOUT: float = 45.0

#: The health probe is cached in the ENVIRONMENT (not a module global) so a
#: long-lived worker and a short-lived script cannot disagree about whether
#: the lane is up.
PROBE_TTL_S: float = 30.0


def _no_cache() -> bool:
    """True when the operator forced a fresh probe on every call."""
    return bool(os.getenv("RAG_EMBED_NO_CACHE"))


def _cached_ok() -> bool | None:
    """The cached verdict, or None when the cache is cold or expired."""
    if _no_cache():
        return None
    probed = os.getenv("_QWEN_PROBED")
    probed_at = os.getenv("_QWEN_PROBED_AT")
    if probed and probed_at and (time.monotonic() - float(probed_at)) < PROBE_TTL_S:
        return os.getenv("_QWEN_OK") == "1"
    return None


def _store_probe(ok: bool) -> None:
    """Write the probe verdict into the environment cache."""
    os.environ["_QWEN_PROBED"] = "1"
    os.environ["_QWEN_PROBED_AT"] = str(time.monotonic())
    os.environ["_QWEN_OK"] = "1" if ok else "0"


async def embed(
    text: str,
    dim: int = EMBEDDING_DIM,
    query: bool = False,
    timeout: float | None = None,  # noqa: ASYNC109 — per-call policy value, not a blocking default
) -> list[float] | None:
    """Embed via the OpenAI-compatible ``/embeddings``.

    Returns None on ANY failure so the chain in ``embeddings._get_embedding_impl``
    falls through to the next lane. Callers that need a hard failure use
    :class:`RAGUnavailableError` at the end of the chain — never an empty
    vector.
    """
    try:
        payload_text = QUERY_INSTRUCT.format(text) if query else text
        async with httpx.AsyncClient(
            timeout=timeout or (QUERY_TIMEOUT if query else DOC_TIMEOUT)
        ) as c:
            r = await c.post(
                f"{QWEN_EMBED_URL}/embeddings",
                json={"model": QWEN_EMBED_MODEL, "input": [payload_text[:MAX_CHARS]]},
            )
            if r.status_code != 200:
                log.debug("int_embed_http_%s", r.status_code)
                return None
            vec = (r.json().get("data") or [{}])[0].get("embedding")
            if not vec or not isinstance(vec, list):
                return None
        return resize(vec, dim)
    except Exception as e:
        log.debug("int_embed_failed: %s", e)
        return None


def reachable() -> bool:
    """Quick probe: is Qwen3-8B listening? Cached per process.

    The cache expires after :data:`PROBE_TTL_S` so a llama.cpp that was still
    cold-starting during the initial probe is retried automatically. Set
    ``RAG_EMBED_NO_CACHE=1`` to force a fresh probe every time.

    Blocking — callers on the event loop MUST wrap it in ``asyncio.to_thread``.
    """
    cached = _cached_ok()
    if cached is not None:
        return cached
    try:
        with httpx.Client(timeout=5.0) as c:
            ok = c.get(f"{QWEN_EMBED_URL}/health").status_code == 200
    except Exception:
        ok = False
    _store_probe(ok)
    if not ok:
        log.info("rag_int_unreachable url=%s", QWEN_EMBED_URL)
    return ok
