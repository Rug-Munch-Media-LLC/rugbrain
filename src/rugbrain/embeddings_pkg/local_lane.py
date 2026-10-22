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
from rugbrain.embeddings_pkg import admission
from rugbrain.embeddings_pkg.normalize import resize

log = logging.getLogger(__name__)

#: Embed text is clipped to this many CHARS before the provider sees it
#: (2048-token budget; see ``rugbrain.token_budget``).
MAX_CHARS: int = 8000

#: Per-call timeouts. Queries must outlive a QUEUED slot: the 8B lane serves
#: two slots at ~8-20s each, so a query behind a few documents needs far more
#: than the old 45s (measured 2026-10-22: 38s with 6 deferred, >120s burst).
DOC_TIMEOUT: float = float(os.getenv("RAG_EMBED_DOC_TIMEOUT", "300"))
QUERY_TIMEOUT: float = float(os.getenv("RAG_EMBED_QUERY_TIMEOUT", "180"))

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


def _auth_headers() -> dict[str, str]:
    """Bearer header for the token-protected local lane, or {} when unkeyed.

    Read per call (not at import) so the lane key can be rotated with a
    container restart alone. The value is never logged.
    """
    key = os.getenv("QWEN_EMBED_API_KEY", "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


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
        async with admission.gate():
            async with httpx.AsyncClient(
                timeout=timeout or (QUERY_TIMEOUT if query else DOC_TIMEOUT)
            ) as c:
                # The :8024 Qwen3-Embedding-8B lane starts with --api-key, so
                # an unauthenticated POST is a hard 401: without this header the
                # last-resort rung could never answer even when the lane was
                # healthy. This was the difference between "no backend" and a
                # working 4096d fallback.
                r = await c.post(
                    f"{QWEN_EMBED_URL}/embeddings",
                    headers=_auth_headers(),
                    json={"model": QWEN_EMBED_MODEL, "input": [payload_text[:MAX_CHARS]]},
                )
            if r.status_code != 200:
                log.warning(
                    "int_embed_http_%s url=%s body=%s",
                    r.status_code,
                    QWEN_EMBED_URL,
                    r.text[:160],
                )
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
    except httpx.TimeoutException:
        # A BUSY lane is not a dead lane: the healthy-but-saturated 8B server
        # answered the probe later than 5s (2026-10-22). Only a refused
        # connection proves nothing is listening; the embed call itself owns
        # the real deadline.
        ok = True
    except Exception:
        ok = False
    _store_probe(ok)
    if not ok:
        log.info("rag_int_unreachable url=%s", QWEN_EMBED_URL)
    return ok
