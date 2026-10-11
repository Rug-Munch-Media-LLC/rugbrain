"""M4 RAG Embeddings — the backend chain and the public async API.

One API, one chain, one vector space:

* Tier 1: DeepInfra Qwen/Qwen3-Embedding-8B (4096d, hosted, keyed) — CANONICAL
* Tier 2: local llama.cpp ``:8004`` — SAME model, so fallback is
  index-compatible.

**FAIL LOUD.** If every lane fails, :class:`RAGUnavailableError` is raised.
There is no hash fallback: T04 removed it because it returned confidently-wrong
(non-semantic) vectors, so a dead backend read as a working search returning
0 results. Anything added to this chain must return ``None`` to fall through
or raise — never an empty vector, never a placeholder.

The lanes themselves live in :mod:`rugbrain.embeddings_pkg` (one HTTP client
each, one job per module). This module owns only the ORDER in which they are
tried, which is the thing that must stay single-sourced.
"""

from __future__ import annotations

import asyncio
import logging
import time

# This module is the PUBLIC embedding surface. The constants below are imported
# only to be re-exported (`__all__` lists them, and ~30 modules plus the test
# suite read them off this module), so ruff must not treat them as dead.
from rugbrain.embeddings_pkg import legacy_lanes as _legacy_lanes
from rugbrain.embeddings_pkg.config import BACKEND as _BACKEND
from rugbrain.embeddings_pkg.config import (
    DEEPINFRA_EMBED_MODEL,
    DEEPINFRA_EMBED_URL,
    EMBED_BUILD_ID,
    EMBED_MODEL_NAME,
    EMBED_MODEL_VERSION,
    EMBED_VERSION_STAMP,
    EMBEDDING_DIM,
    GATEWAY_EMBED_URL,
    NIM_KEY,
    NIM_MODELS,
    NIM_URL,
    OPENROUTER_MODEL,
    OPENROUTER_URL,
    QUERY_INSTRUCT,
    QWEN_EMBED_MODEL,
    QWEN_EMBED_URL,
    RAGUnavailableError,
)
from rugbrain.embeddings_pkg.hosted_lane import embed as deepinfra_embed
from rugbrain.embeddings_pkg.hosted_lane import ready as deepinfra_ready
from rugbrain.embeddings_pkg.legacy_lanes import (
    gateway_embed,
    nim_embed,
    openrouter_embed,
    or_key_pool,
)
from rugbrain.embeddings_pkg.local_lane import embed as int_embed
from rugbrain.embeddings_pkg.local_lane import reachable as int_reachable
from rugbrain.embeddings_pkg.normalize import resize as resize_fn
from rugbrain.embeddings_pkg.siliconflow_lane import embed as siliconflow_embed

log = logging.getLogger(__name__)

#: Backward-compatible lane aliases. Tests and operator harnesses monkeypatch
#: these NAMES on this module (not the lane modules), so the chain below
#: resolves lanes through ``globals()``. Without this block a patch would
#: silently miss and the test would pass against the REAL provider.
_int_embed = int_embed
_deepinfra_embed = deepinfra_embed
_gateway_embed = gateway_embed
_openrouter_embed = openrouter_embed
_nim_embed = nim_embed
_siliconflow_embed = siliconflow_embed
_int_reachable = int_reachable
_deepinfra_ready = deepinfra_ready
_or_key_pool = or_key_pool
_resize = resize_fn
_OR_KEY_CURSOR = _legacy_lanes._OR_KEY_CURSOR

#: Lane name -> the module-global holding its callable. The chain resolves
#: each lane through ``globals()`` at CALL time, never through a snapshot of
#: the function objects: a test or operator harness that patches
#: `embeddings._deepinfra_embed` must patch the chain that actually runs. A
#: lane captured at import time would let such a patch silently miss and the
#: test then pass against the REAL provider.
_LANE_GLOBALS: dict[str, str] = {
    "deepinfra": "_deepinfra_embed",
    "int": "_int_embed",
    "gateway": "_gateway_embed",
    "openrouter": "_openrouter_embed",
    "nim": "_nim_embed",
    "siliconflow": "_siliconflow_embed",
}

#: The `auto` chain, in order. DeepInfra first (hosted, warm, no CPU
#: contention), local :8004 second (same model ⇒ same space).
#: NOT in this chain: `siliconflow` (credential 401-dead as of 2026-10-02,
#: gated off by SILICONFLOW_ENABLED=0). A gated-off rung returns None and
#: falls through, so it is safe in `_OVERRIDE_CHAINS` but adding it here
#: would only add a guaranteed-miss HTTP round trip to every request.
AUTO_CHAIN: tuple[str, ...] = ("deepinfra", "int")

#: Explicit ``RAG_EMBED_BACKEND`` overrides and their fall-through order. An
#: override names a primary lane plus, optionally, a never-go-down secondary.
_OVERRIDE_CHAINS: dict[str, tuple[str, ...]] = {
    "deepinfra": ("deepinfra",),
    "int": ("int",),
    "gateway": ("gateway",),
    # The OR pool is capped (1k/day/account) and NIM is a separate free quota,
    # so an exhausted OR pool is never allowed to be the end of the road.
    "openrouter": ("openrouter", "nim"),
    "nim": ("nim",),
    # 2026-10-11: the credential was rotated and the lane is proven 4096-dim
    # (cosine 0.9975 against the DeepInfra vectors it replaces, so the live
    # collections stay queryable with no re-embed). The chain is all three
    # hosted providers: DeepInfra is last while it is out of credit and
    # rejoins the head by itself once topped up, with no code change.
    "siliconflow": ("siliconflow", "openrouter", "deepinfra"),
    # The full chain without pinning a primary, for operators who would rather
    # take whatever answers first than hold a lane at the head.
    "hosted": ("siliconflow", "openrouter", "deepinfra"),
}


async def _call_lane(
    lane: str,
    text: str,
    query: bool,
    timeout: float | None = None,  # noqa: ASYNC109 - policy value
) -> list[float] | None:
    """Invoke one lane, or None when the lane is not registered.

    Resolved from ``globals()`` at call time so a monkeypatch on
    ``embeddings._<lane>_embed`` is honoured. The ``TypeError`` fallback keeps
    the legacy lanes callable: they take ``(text)``/``(text, dim)`` only and
    reject the ``query``/``timeout`` kwargs.
    """
    func = globals().get(_LANE_GLOBALS.get(lane, ""))
    if func is None:
        log.warning("embed_lane_unknown lane=%s", lane)
        return None
    try:
        result = func(text, query=query, timeout=timeout)
    except TypeError:
        # Legacy rungs take (text) or (text, dim) only. They may be sync OR
        # async, so the fallback must resolve both — awaiting a bare list
        # raises "object list can't be used in 'await' expression" and takes
        # every sync legacy lane down with it.
        result = func(text)
    if asyncio.iscoroutine(result):
        return await result  # type: ignore[no-any-return]
    return result  # type: ignore[no-any-return]


async def _get_embedding_impl(text: str, query: bool = False) -> list[float]:
    """Core embedding resolution (separated for latency instrumentation).

    2026-08-29 consolidation: auto = DeepInfra → local :8004 → fail loud.
    Both lanes serve the SAME qwen3-embedding-8b ⇒ one 4096 vector space, so
    fallback never breaks index compatibility. Gateway (:8010), OpenRouter and
    NIM are DROPPED from auto — legacy lanes kept only for the explicit
    ``RAG_EMBED_BACKEND`` overrides above.
    """
    chain = _OVERRIDE_CHAINS.get(_BACKEND, AUTO_CHAIN)
    # The local probe is sync and cached; run it off the event loop so a slow
    # first probe can never wedge the request handler.
    timeout = 45.0 if query else None
    for lane in chain:
        if lane == "int" and not await asyncio.to_thread(_int_reachable):
            continue
        vector = await _call_lane(lane, text, query, timeout)
        if vector is not None:
            return vector  # type: ignore[return-value]

    raise RAGUnavailableError(
        f"no embedding backend available (chain={chain} exhausted). "
        "Per T04, hash fallback removed to prevent false-confidence in RAG results. "
        f"Verify: DEEPINFRA_API_KEY set? QWEN_EMBED_URL={QWEN_EMBED_URL} "
        "qwen3-embedding-8b loaded?"
    )


async def get_embedding(text: str, query: bool = False) -> list[float]:
    """Async embedding API. Returns a 4096d float list.

    Per T04: NO hash fallback. If every lane fails, raises
    :class:`RAGUnavailableError`. The RAG endpoint translates this to HTTP 503
    + ``{"status": "empty", "chunks": [], "message": ...}``.

    Set ``query=True`` for QUERY-side text — prepends the Qwen3 ``Instruct:``
    template the model was trained with (documents stay raw).
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
    into collections must NOT use this — feed them raw via :func:`get_embedding`.
    """
    return await get_embedding(query, query=True)


async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """Batch embedding. Sequential — llama.cpp and OpenRouter handle larger
    requests poorly and we'd rather keep memory bounded."""
    return [await get_embedding(t) for t in texts]


def current_backend() -> str:
    """Which tier is currently active? For observability."""
    if _BACKEND != "auto":
        return _BACKEND
    if deepinfra_ready():
        return "deepinfra"
    if int_reachable():
        return "int"
    return "unavailable"


__all__ = [
    "DEEPINFRA_EMBED_MODEL",
    "DEEPINFRA_EMBED_URL",
    "EMBEDDING_DIM",
    "EMBED_BUILD_ID",
    "EMBED_MODEL_NAME",
    "EMBED_MODEL_VERSION",
    "EMBED_VERSION_STAMP",
    "GATEWAY_EMBED_URL",
    "NIM_KEY",
    "NIM_MODELS",
    "NIM_URL",
    "OPENROUTER_MODEL",
    "OPENROUTER_URL",
    "QUERY_INSTRUCT",
    "QWEN_EMBED_MODEL",
    "QWEN_EMBED_URL",
    "RAGUnavailableError",
    "current_backend",
    "get_embedding",
    "get_embeddings",
    "get_query_embedding",
]
