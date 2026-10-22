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

#: The ``auto`` chain, in order. Every rung serves the SAME
#: ``Qwen/Qwen3-Embedding-8B`` @ 4096d, so a fall-through never changes the
#: vector space the collections were built in; order is purely about latency
#: and liveness. Cross-model lanes (gateway, OpenRouter, NIM) are NEVER here —
#: they are reachable only through an explicit ``RAG_EMBED_BACKEND`` override.
#:
#: 2026-10-22 (measured): SiliconFlow LEADS — it is the only hosted lane with a
#: live 200 @ 4096 credential (api.siliconflow.com; the .cn host 401s the same
#: key). DeepInfra is second and 401s "user not authorized", the local :8024 8B
#: lane is last because it is slow (9-44 s/query on CPU) but always ours.
AUTO_CHAIN: tuple[str, ...] = ("siliconflow", "deepinfra", "int")

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
    # 2026-10-12 (operator directive): DeepInfra PRIMARY, SiliconFlow backup,
    # OpenRouter backup -- on BOTH the query and document lanes, matching
    # ContextCrypto. This chain previously led with siliconflow on a stale
    # "DeepInfra is out of credit" note. DeepInfra has been topped up and
    # re-proved live (4096-dim embed + rerank), but queries kept embedding on
    # SiliconFlow while the corpus was embedded on DeepInfra -- two providers,
    # one model name, a different vector space. Any collection whose top
    # similarity sat near the 0.4 threshold then silently returned zero hits
    # (measured: social_intel 0.519 stored vs sub-threshold served).
    # Chain order MUST match the lane the corpus was embedded with.
    "siliconflow": ("deepinfra", "siliconflow", "openrouter"),
    # The full chain without pinning a primary, for operators who would rather
    # take whatever answers first than hold a lane at the head.
    "hosted": ("deepinfra", "siliconflow", "openrouter"),
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
    """Which lane the chain would serve from right now (observability).

    2026-10-22: this answered "deepinfra" while SiliconFlow was doing the work,
    because it only knew the two original rungs. A backend probe that names the
    wrong provider is worse than none — it sent every 503 triage down the wrong
    lane. Reports the FIRST usable rung, i.e. AUTO_CHAIN order.
    """
    if _BACKEND != "auto":
        return _BACKEND
    from rugbrain.embeddings_pkg import siliconflow_lane as _sf

    if _sf.SILICONFLOW_ENABLED and _sf._api_key():
        return "siliconflow"
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
