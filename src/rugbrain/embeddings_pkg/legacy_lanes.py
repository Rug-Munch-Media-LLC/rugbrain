"""LEGACY embedding lanes — gateway :8010, OpenRouter, NVIDIA NIM.

Dropped from the ``auto`` chain at the 2026-08-29 consolidation. They survive
only as explicit ``RAG_EMBED_BACKEND`` overrides, and every one of them
returns None on failure so the caller fails loud per T04 rather than
degrading to a wrong vector space.

Cross-model responses are REFUSED, not normalised: a vector from a different
model is a different space, and padding it into this index poisons ranking in a
way no downstream check can see.
"""

from __future__ import annotations

import itertools
import logging
import os

import httpx

from rugbrain.embeddings_pkg.config import (
    EMBEDDING_DIM,
    GATEWAY_EMBED_URL,
    NIM_KEY,
    NIM_MODELS,
    NIM_URL,
    OPENROUTER_MODEL,
    OPENROUTER_URL,
    QUERY_INSTRUCT,
)
from rugbrain.embeddings_pkg.normalize import resize

log = logging.getLogger(__name__)

#: Embed text is clipped to this many CHARS before the provider sees it.
MAX_CHARS: int = 8000

#: Gateway lane timeout (it breaks to a cloud fallback of its own).
GATEWAY_TIMEOUT: float = 75.0
#: Cloud lanes fail fast — a legacy rung must not outlive its usefulness.
CLOUD_TIMEOUT: float = 15.0

#: Round-robin cursor across the OpenRouter account pool.
_OR_KEY_CURSOR = itertools.count()


def or_key_pool() -> list[str]:
    """OpenRouter key pool for the free tier.

    ``OPENROUTER_API_KEYS`` (comma-separated) wins; falls back to the single
    legacy ``OPENROUTER_API_KEY``. Multiple accounts multiply free-tier budget
    and give 429 failover (2026-08-25 directive: use both).
    """
    keys = [k.strip() for k in os.getenv("OPENROUTER_API_KEYS", "").split(",") if k.strip()]
    if not keys:
        single = os.getenv("OPENROUTER_API_KEY", "").strip()
        if single:
            keys = [single]
    return keys


async def gateway_embed(
    text: str, dim: int = EMBEDDING_DIM, query: bool = False
) -> list[float] | None:
    """Model Services Gateway :8010 lane (breaker + cloud fallback inside).

    Tier is chosen by DIM COMPATIBILITY with the target index: 4096 -> deep
    lane, anything else -> speed lane. Cloud-served responses are REFUSED for
    indexing unless DeepInfra served them (same model ⇒ same space).
    """
    tier = "deep" if dim >= 2048 else "speed"
    try:
        payload_text = QUERY_INSTRUCT.format(text) if query else text
        async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as c:
            r = await c.post(
                f"{GATEWAY_EMBED_URL}/embed",
                json={"text": payload_text[:MAX_CHARS], "tier": tier},
            )
            if r.status_code != 200:
                return None
            d = r.json()
        vecs = d.get("embeddings") or []
        vec = vecs[0] if vecs else None
        if not vec:
            return None
        if d.get("served_by") == "cloud":
            # 2026-08-29 consolidation: the gateway's deep cloud lane is now
            # DeepInfra Qwen/Qwen3-Embedding-8B — the SAME model as local
            # :8004 ⇒ its vectors are index-compatible. Cross-model cloud
            # (legacy qwen3-8b / liquid / nemotron lanes) is still refused.
            cloud_model = str(d.get("cloud_model", ""))
            same_model = cloud_model.split("/")[-1].lower() == "qwen3-embedding-8b"
            if not same_model:
                log.warning(
                    "gateway_served_cloud tier=%s model=%s dims=%s — refused for "
                    "index use (cross-model space)",
                    tier,
                    d.get("cloud_model"),
                    d.get("model_dims"),
                )
                return None
            log.info("gateway deep lane served by same-model cloud (deepinfra) — index-compatible")
        return resize(vec, dim)
    except Exception as e:
        log.debug("gateway_embed_failed: %s", e)
        return None


async def openrouter_embed(text: str, dim: int = EMBEDDING_DIM) -> list[float] | None:
    """Try OpenRouter Nemotron across the account key pool.

    Round-robin start spreads load across accounts; a 429 rotates to the next
    key before giving up. None on total failure.
    """
    keys = or_key_pool()
    if not keys:
        return None
    start = next(_OR_KEY_CURSOR) % len(keys)
    ordered = keys[start:] + keys[:start]
    for i, key in enumerate(ordered):
        try:
            async with httpx.AsyncClient(timeout=CLOUD_TIMEOUT) as c:
                r = await c.post(
                    OPENROUTER_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": OPENROUTER_MODEL, "input": text[:MAX_CHARS]},
                )
                if r.status_code in (401, 402, 403, 404, 429):
                    # 402/404 are per-account states (no credits, no endpoint):
                    # rotate exactly like a 429 so one dead account cannot
                    # shadow a funded backup key.
                    log.warning(
                        "openrouter_key_unusable status=%s key_tail=%s trying_next=%s",
                        r.status_code,
                        key[-4:],
                        i + 1 < len(ordered),
                    )
                    continue
                if r.status_code != 200:
                    log.debug("openrouter_embed_http_%s", r.status_code)
                    return None
                vec = (r.json().get("data") or [{}])[0].get("embedding")
                if not vec:
                    return None
                return resize(vec, dim)
        except Exception as e:
            log.debug("openrouter_embed_failed: %s", e)
            continue
    return None


async def nim_embed(text: str, dim: int = EMBEDDING_DIM) -> list[float] | None:
    """NVIDIA NIM direct tier — free credits independent of OpenRouter.

    Tries NIM_MODELS in order (429/5xx rotates). Returns None on total failure
    so the caller can fail loud per T04.
    """
    if not NIM_KEY or not NIM_MODELS:
        return None
    for model in NIM_MODELS:
        try:
            async with httpx.AsyncClient(timeout=CLOUD_TIMEOUT) as c:
                r = await c.post(
                    NIM_URL,
                    headers={"Authorization": f"Bearer {NIM_KEY}"},
                    json={
                        "input": [text[:MAX_CHARS]],
                        "model": model,
                        "input_type": "query",
                        "encoding_format": "float",
                        "truncate": "END",
                    },
                )
                if r.status_code in (429, 500, 502, 503):
                    log.warning("nim_rate_limited model=%s trying_next", model)
                    continue
                if r.status_code != 200:
                    log.debug("nim_embed_http_%s model=%s", r.status_code, model)
                    continue
                vec = (r.json().get("data") or [{}])[0].get("embedding")
                if not vec:
                    continue
                return resize(vec, dim)
        except Exception as e:
            log.debug("nim_embed_failed model=%s: %s", model, e)
            continue
    return None
