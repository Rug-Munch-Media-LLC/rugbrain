"""The embed-lane health check the operator reads during a lane flip.

One job: classify which embedding lane is serving RIGHT NOW (``embed_lane``),
prove the vector the public chain produces is a real 4096-d corpus vector, and
fail loudly when the lane answers with an empty vector, a 1024-d vector
(supermemory's 0.6B lane on :8004) or any other retired width.

Read-only: one backend probe plus one real embedding call through the public
chain. Never writes to Qdrant — ``rugbrain.eval.dim_probe`` owns the store side.

2026-10-24 (DeepInfra 402 / SiliconFlow disabled): the local ``llama-embed8b``
lane (:8024) became the primary via ``RAG_EMBED_BACKEND=int``. This module is
the loud answer to "which lane is actually embedding?" — a backend probe that
reports silently is how the fleet spent a session believing DeepInfra was
alive while it returned 402 on every call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

#: Same rule as eval.dim_probe: a real sentence, never an empty or
#: punctuation-only string (some providers route those down a different path).
PROBE_TEXT = "embed-lane health probe: qwen3-embedding-8b lane check"

__all__ = ["PROBE_TEXT", "embed_lane_snapshot"]


async def embed_lane_snapshot() -> dict[str, Any]:
    """Classify the serving lane and prove its vector width. Raises when down.

    Returns ``{"embed_lane": local|hosted|unknown, "backend": <chain name>,
    "dim": <int>, "probe_ok": True, "latency_ms": <float>}``.

    Loud failures (per T04, the same policy as the embedding chain):
    * empty input / no reachable lane / width refused → the chain raises
      :class:`RAGUnavailableError`, which this propagates unchanged;
    * a vector of any width other than ``EMBEDDING_DIM`` (empty or 1024-d)
      fails RIGHT HERE, before any caller gets a bad vector.
    """
    from rugbrain.embeddings import current_backend, get_embedding  # call-time

    # imports so a monkeypatch on rugbrain.embeddings is honoured, and
    # so an unrelated import failure cannot kill the health route at boot.
    from rugbrain.embeddings_pkg.config import EMBEDDING_DIM

    backend = await asyncio.to_thread(current_backend)
    if backend == "int":
        embed_lane = "local"
    elif backend == "unavailable":
        embed_lane = "unknown"
    else:
        embed_lane = "hosted"

    t0 = time.perf_counter()
    vector = await get_embedding(PROBE_TEXT)
    latency_ms = (time.perf_counter() - t0) * 1000.0

    dim = len(vector)
    if dim != EMBEDDING_DIM:
        raise RuntimeError(
            f"embed-lane width guard: got {dim}d want {EMBEDDING_DIM}d on lane={backend}. "
            "A 1024d vector is supermemory's 0.6B lane (:8004) leaking into the corpus "
            "chain — check QWEN_EMBED_URL and RAG_EMBED_BACKEND."
        )

    log.info(
        "embed_lane_health embed_lane=%s backend=%s dim=%d latency_ms=%.0f",
        embed_lane,
        backend,
        dim,
        latency_ms,
    )
    return {
        "embed_lane": embed_lane,
        "backend": backend,
        "dim": dim,
        "probe_ok": True,
        "latency_ms": round(latency_ms, 1),
    }
