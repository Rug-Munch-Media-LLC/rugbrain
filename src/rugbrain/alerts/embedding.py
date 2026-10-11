"""Embedding lane — Qwen3-8B via llama.cpp, L2-normalized."""

from __future__ import annotations

import logging

import httpx

from rugbrain.dim_policy import normalize_dims

from . import config

log = logging.getLogger("rugbrain.alerts")


async def _embed(text: str) -> list[float] | None:
    """Embed *text* through Qwen3-8B (llama.cpp) (L2-normalized). Returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(
                f"{config.QWEN_EMBED_URL}/embeddings",
                json={"model": config.QWEN_EMBED_MODEL_NAME, "input": [text[:8000]]},
            )
            if r.status_code != 200:
                return None
            data = r.json().get("data", [])
            if not data:
                return None
            vec = data[0].get("embedding", [])
            if not isinstance(vec, list) or not vec:
                return None
            # Central dim policy: MRL-truncate oversize + L2 renormalize;
            # undersize raises (no silent zero-pad — padded vectors poison
            # cosine ranking). Replaces the old inline truncate/pad block.
            return normalize_dims([float(x) for x in vec], config.EMBEDDING_DIM)
    except Exception as e:
        log.debug("alert_embed_failed: %s", e)
        return None
