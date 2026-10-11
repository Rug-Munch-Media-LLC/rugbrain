"""Batched embedding for the ``rug_events`` build.

The build's hot path is 1,877 short texts. The obvious call — ``embed_one`` in a
loop — serialises 1,877 HTTP round-trips (minutes, and every one a chance to
time out). ``embed()`` posts the whole page in one request, which is the
difference between minutes and seconds.

Lane correctness matters more than speed here, so it is asserted rather than
assumed. The corpus (``defi_exploits``, ``exploits``, ``scam_intel_v2``, every
collection this projection is read from and graded against) is **2560-dim**
Qwen3-Embedding-8B. Two environment hazards silently break that:

  * ``DEEPINFRA_EMBED_URL`` shipped pointing at the full ``.../v1/embeddings``
    path while ``Qwen3Embedder`` appends ``/embeddings`` itself — every call
    404s on a doubled path. Fixed below.
  * ``DEEPINFRA_EMBED_MODEL=Qwen/Qwen3-Embedding-8B`` returns 4096-dim, which
    cannot be upserted into a 2560-dim collection. Truncating an 8B vector into
    a 4B collection produces vectors that *store* and *search* but are
    meaningless, so the build refuses instead.

The 8B lane belongs to a concurrent migration on this host; this build pins the
lane the corpus is actually in and says so when it does.
"""

from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger(__name__)

#: The dim every collection this build reads from or grades against.
CORPUS_DIM = 2560

#: The model the 2560-dim corpus was built with.
CORPUS_MODEL = "Qwen/Qwen3-Embedding-8B"

#: Environment forced onto the corpus lane, overriding whatever the shell has.
#: Only set when the current value is missing or belongs to another lane.
LANE_ENV: dict[str, str] = {
    "DEEPINFRA_EMBED_URL": "https://api.deepinfra.com/v1",
    "DEEPINFRA_EMBED_MODEL": CORPUS_MODEL,
    "RAG_EMBED_MODEL": "qwen3-embedding-8b",
    "QWEN_EMBED_DIMS": str(CORPUS_DIM),
}


class DimMismatch(RuntimeError):
    """The embedder returned a vector the corpus cannot hold."""


def apply_lane_env() -> list[str]:
    """Pin the embed lane to the corpus's 2560-dim lane. Returns changed keys.

    This must run BEFORE the first ``import rugbrain.*``: ``rugbrain/__init__``
    pulls in ``qwen_embedders``, which snapshots the URL and model at import
    time. Pinning after that import is a no-op, which is exactly the silent
    404 this module exists to stop. ``scripts/build_rug_events.py`` calls it at
    the top of the file for that reason.
    """
    changed: list[str] = []
    for key, value in LANE_ENV.items():
        if os.getenv(key) != value:
            os.environ[key] = value
            changed.append(key)
    if changed:
        log.info("embed_lane_pinned keys=%s dim=%d", ",".join(changed), CORPUS_DIM)
    return changed


async def embed_batch(texts: list[str], concurrency: int = 4) -> list[list[float]]:
    """Embed *texts* in pages, one HTTP request per page. Order is preserved.

    Pages rather than one giant request: DeepInfra caps the request body, and a
    page size that is comfortably under it still keeps the round-trip count in
    the tens instead of the thousands.
    """
    apply_lane_env()
    from rugbrain.embedder import get_embedder

    embedder = get_embedder()
    page = _page_size(embedder)
    out: list[list[float]] = []
    for start in range(0, len(texts), page):
        window = texts[start : start + page]
        out.extend(await embedder.embed(window))
        log.info("embed_progress done=%d total=%d", len(out), len(texts))
    _assert_dim(out)
    return out


def embed_sync(texts: list[str]) -> list[list[float]]:
    """Blocking wrapper for the build script (no running loop expected)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(embed_batch(texts))
    raise RuntimeError("embed_sync must not be called from a running loop")


def _page_size(embedder: object) -> int:
    """Texts per request. Read from the embedder when it exposes a hint."""
    for attr in ("MAX_BATCH", "BATCH", "batch_size"):
        value = getattr(embedder, attr, None)
        if isinstance(value, int) and value > 0:
            return value
    return 128


def _assert_dim(vectors: list[list[float]]) -> None:
    """Refuse vectors that do not fit the corpus. Storing them would be worse."""
    if not vectors:
        return
    dims = {len(v) for v in vectors}
    if dims != {CORPUS_DIM}:
        raise DimMismatch(
            f"embedder returned dim(s) {sorted(dims)}, corpus is {CORPUS_DIM}. "
            f"Set DEEPINFRA_EMBED_MODEL={CORPUS_MODEL} and "
            "DEEPINFRA_EMBED_URL=https://api.deepinfra.com/v1 (the URL must NOT "
            "include the /embeddings suffix — the client appends it)."
        )


__all__ = [
    "CORPUS_DIM",
    "CORPUS_MODEL",
    "DimMismatch",
    "apply_lane_env",
    "embed_batch",
    "embed_sync",
]
