"""HTTP transport for the Qwen3-Embedding-4B lanes (DeepInfra + llama.cpp).

Split from the provider class so the wire shape and the dim rule are testable
without constructing a provider. Every failure is re-raised as a named
``RuntimeError`` — a silent ``[]`` here is what turned a down embedder into
"search works, nothing matches".
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

from rugbrain.dim_policy import normalize_dims

logger = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 8000
_TIMEOUT_S = 120


class _Lane(Protocol):
    """What the transport needs from a provider — keeps this module decoupled."""

    dims: int
    api_key: str
    url: str
    MODEL: str


def _fit(vec: list[float], dims: int, label: str) -> list[float]:
    """Match a raw vector to ``dims`` or raise — never silently truncate short."""
    if len(vec) == dims:
        return vec
    if len(vec) > dims:
        # MRL truncate + renormalize (dim_policy)
        return normalize_dims(vec, dims)
    raise RuntimeError(f"qwen_embedder dim mismatch: got {len(vec)}, want >= {dims} ({label})")


def deepinfra_model() -> str:
    """Re-read the model from the env on every call (import-time snapshots lie)."""
    from rugbrain.embedder_providers.qwen3 import DEEPINFRA_EMBED_MODEL

    return os.getenv("DEEPINFRA_EMBED_MODEL") or DEEPINFRA_EMBED_MODEL


def deepinfra_url() -> str:
    """Re-read the URL from the env on every call (import-time snapshots lie)."""
    from rugbrain.embedder_providers.qwen3 import _embed_url

    return _embed_url()


async def post_deepinfra(lane: Any, texts: list[str]) -> list[list[float]]:
    """Hosted DeepInfra lane — batched OpenAI-compatible /embeddings."""
    import httpx

    headers = {
        "Authorization": f"Bearer {lane.api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": deepinfra_model(), "input": [t[:_MAX_INPUT_CHARS] for t in texts]}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(deepinfra_url(), headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"qwen_embedder deepinfra HTTP {resp.status_code}: {resp.text[:200]}"
                )
            data = resp.json().get("data", [])
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"qwen_embedder deepinfra failed: {exc}") from exc

    results = [
        _fit(item.get("embedding", []), lane.dims, "deepinfra")
        for item in sorted(data, key=lambda x: x.get("index", 0))
    ]
    if len(results) != len(texts):
        raise RuntimeError(
            f"deepinfra embed returned {len(results)} vectors for {len(texts)} inputs"
        )
    return results


async def post_llamacpp(lane: Any, texts: list[str]) -> list[list[float]]:
    """Local llama.cpp lane — one POST per text (the server takes no batch)."""
    import httpx

    results = []
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        for text in texts:
            try:
                resp = await client.post(
                    f"{lane.url}/embeddings",
                    json={"model": lane.MODEL, "input": [text[:_MAX_INPUT_CHARS]]},
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    if not data:
                        raise RuntimeError("qwen_embedder returned no data")
                    results.append(_fit(data[0].get("embedding", []), lane.dims, "llamacpp"))
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(f"qwen_embedder failed: {exc}") from exc
    return results
