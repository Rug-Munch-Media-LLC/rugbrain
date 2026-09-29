"""DeepInfra hosted lane — the CANONICAL primary embedder.

CANONICAL primary: DeepInfra hosts the exact same Qwen3-Embedding-8B the local
:8004 lane serves — 4096-dim, identical vector space, always warm, no local
CPU contention (~23 ms/doc vs ~1947 ms/doc local, benchmarked 2026-08-29).

Because the models match, falling back to :8004 never breaks index
compatibility. A provider returning MORE than the target width is
MRL-truncated; a NARROWER vector is refused by :func:`resize` per T04.
"""

from __future__ import annotations

import logging

import httpx

from rugbrain.embeddings_pkg.config import (
    DEEPINFRA_EMBED_MODEL,
    DEEPINFRA_EMBED_URL,
    EMBEDDING_DIM,
    QUERY_INSTRUCT,
)
from rugbrain.embeddings_pkg.normalize import resize, truncate_mrl

log = logging.getLogger(__name__)

#: Embed text is clipped to this many CHARS before the provider sees it.
MAX_CHARS: int = 8000

#: Per-call timeout. Queries fail fast; a cold 8B cold-start may take longer.
DOC_TIMEOUT: float = 30.0
QUERY_TIMEOUT: float = 45.0


def ready() -> bool:
    """Is the hosted lane configured? Key resolvable from env or secrets file.

    An unkeyed lane reports NOT ready so the chain prefers local :8004 rather
    than burning a request that is certain to 401.
    """
    try:
        from rugbrain.multi_embedder import _load_api_key

        return bool(_load_api_key("DEEPINFRA_API_KEY"))
    except Exception:
        return False


async def embed(
    text: str,
    dim: int = EMBEDDING_DIM,
    query: bool = False,
    timeout: float | None = None,  # noqa: ASYNC109 — per-call policy value, not a blocking default
) -> list[float] | None:
    """Embed via DeepInfra's OpenAI-compatible ``/embeddings``.

    Returns None on any failure (no key, non-200, malformed body, exception) so
    ``embeddings._get_embedding_impl`` falls through to the next lane. It never
    returns a placeholder vector: the T04 contract is loud failure or a real
    embedding, never a silently wrong one.
    """
    try:
        from rugbrain.multi_embedder import _load_api_key

        key = _load_api_key("DEEPINFRA_API_KEY")
        if not key:
            log.debug("deepinfra_embed_skipped no_api_key")
            return None
        payload_text = QUERY_INSTRUCT.format(text) if query else text
        async with httpx.AsyncClient(
            timeout=timeout or (QUERY_TIMEOUT if query else DOC_TIMEOUT)
        ) as c:
            r = await c.post(
                DEEPINFRA_EMBED_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": DEEPINFRA_EMBED_MODEL, "input": [payload_text[:MAX_CHARS]]},
            )
            if r.status_code != 200:
                log.debug("deepinfra_embed_http_%s", r.status_code)
                return None
            vec = (r.json().get("data") or [{}])[0].get("embedding")
            if not vec or not isinstance(vec, list):
                return None
    except Exception as e:
        log.debug("deepinfra_embed_failed: %s", e)
        return None
    # A wider vector is MRL-truncate-down (mirrors multi_embedder); a narrower
    # one reaches resize() and raises there rather than being padded.
    return truncate_mrl(vec, dim) or resize(vec, dim)
