"""The 8B canary embedding call: credential, HTTP, dimension check.

One job — produce exactly one verified ``CANARY_DIM``-dimensional vector or
raise. It is deliberately a separate module from the config gate so the
credential and the provider call cannot be reached by a route that only wanted
to ask "is this collection eligible?".

A failed canary must never silently fall back to a 2560-dim vector: every
failure path raises rather than degrading.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Iterable
from typing import Any

import httpx

from rugbrain.canary_config import (
    CANARY_DIM,
    CANARY_MODEL,
    CanaryConfigError,
    CanaryUnavailable,
    canary_enabled,
)


def validate_canary_vector(vector: Iterable[Any]) -> list[float]:
    """Validate and copy a 4096-dimensional finite vector."""
    values = [float(item) for item in vector]
    if len(values) != CANARY_DIM:
        raise CanaryConfigError("8B canary returned an unexpected vector dimension")
    if not all(math.isfinite(item) for item in values):
        raise CanaryConfigError("8B canary returned a non-finite vector")
    return values


#: Key-file locations tried, in order, when no env credential is present.
KEY_FILE_PATHS: tuple[str, ...] = (
    "/srv/secrets/deepinfra_api_key",
    "/run/secrets/deepinfra_api_key",
)


def _api_key() -> str:
    """Read the provider key without logging or returning it to callers."""
    value = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if value:
        return value
    paths = (os.getenv("DEEPINFRA_KEY_FILE", ""), *KEY_FILE_PATHS)
    for raw_path in paths:
        if not raw_path:
            continue
        try:
            with open(raw_path, encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError:
            continue
        if value:
            return value
    raise CanaryUnavailable("8B canary embedding credential is unavailable")


async def get_canary_query_embedding(text: str) -> list[float]:
    """Fetch one verified 4096-dimensional canary vector.

    This function is intentionally separate from the canonical embedder. A
    failed canary must never silently fall back to a 2560 vector.
    """
    if not canary_enabled():
        raise CanaryConfigError("8B canary is disabled")
    url = os.getenv("RAG_8B_EMBED_URL", "https://api.deepinfra.com/v1/embeddings")
    model = os.getenv("RAG_8B_EMBED_MODEL", CANARY_MODEL)
    timeout = float(os.getenv("RAG_8B_EMBED_TIMEOUT", "120"))
    payload = {"model": model, "input": [text]}
    try:
        api_key = await asyncio.to_thread(_api_key)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            body = response.json()
        vector = body["data"][0]["embedding"]
        return validate_canary_vector(vector)
    except CanaryConfigError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, httpx.HTTPError) as exc:
        raise CanaryUnavailable("8B canary embedding is unavailable") from exc


__all__ = ["get_canary_query_embedding", "validate_canary_vector"]
