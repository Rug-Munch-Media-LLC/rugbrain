"""Default-off, per-collection 8B canary routing.

The canonical RugBrain path remains Qwen3-Embedding-4B at 2560 dimensions.
This module only enables an explicitly configured, read-only canary for
collections that have a complete 4096-dimensional target. It does not change
ingestion, global collection configuration, or the separate 8B migration.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Iterable, Mapping
from typing import Any

import httpx

from rugbrain.knowledge_boundary import sanitize_public_result

CANARY_DIM = 4096
CANARY_MODEL = "Qwen/Qwen3-Embedding-8B"
CANARY_SUFFIX = "_8b"

# Keep this list small and explicit. These are public RugBrain
# intelligence collections only; do not add ContextCrypto/internal or legacy
# non-public collections here. The public boundary must not become a raw code
# bridge.
ALLOWED_CANARY_SOURCES = frozenset(
    {
        "contract_audits",
        "contract_security",
        "defi_exploits",
        "hacks_v2",
        "known_scams",
        "market_health",
        "regulation_enforcement",
        "scam_patterns",
        "stablecoin_market",
        "wallet_profiles",
        "wallet_v2",
    }
)


class CanaryConfigError(ValueError):
    """Raised when canary configuration is invalid or targets an unsafe source."""


class CanaryUnavailable(RuntimeError):
    """Raised when the explicitly enabled canary lane cannot serve a query."""


def _enabled() -> bool:
    return os.getenv("RAG_8B_CANARY_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def _configured_sources() -> frozenset[str]:
    raw = os.getenv("RAG_8B_CANARY_COLLECTIONS", "")
    return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


def canary_enabled() -> bool:
    """Whether the explicit canary switch is on."""
    return _enabled()


def canary_target_for(collection: str) -> str | None:
    """Return a target only for an explicitly configured safe source.

    Disabled mode is a no-op, preserving the canonical 2560 route. Enabling
    the switch with an incomplete/internal/unknown collection is a config
    error, not a request to fall back to a different vector space.
    """
    normalized = str(collection or "").strip()
    if not _enabled():
        return None
    configured = canary_collections()
    if not configured:
        raise CanaryConfigError("RAG_8B_CANARY_COLLECTIONS is required when canary is enabled")
    if normalized not in configured:
        return None
    if normalized not in ALLOWED_CANARY_SOURCES:
        raise CanaryConfigError("requested collection is not eligible for the 8B canary")
    return f"{normalized}{CANARY_SUFFIX}"


def canary_collections() -> frozenset[str]:
    """Return configured canary sources after validating each one."""
    if not _enabled():
        return frozenset()
    configured = _configured_sources()
    invalid = configured - ALLOWED_CANARY_SOURCES
    if invalid:
        raise CanaryConfigError("one or more canary collections are not allowlisted")
    return configured


def validate_canary_vector(vector: Iterable[Any]) -> list[float]:
    """Validate and copy a 4096-dimensional finite vector."""
    values = [float(item) for item in vector]
    if len(values) != CANARY_DIM:
        raise CanaryConfigError("8B canary returned an unexpected vector dimension")
    if not all(math.isfinite(item) for item in values):
        raise CanaryConfigError("8B canary returned a non-finite vector")
    return values


def sanitize_canary_result(value: Any, source_collection: str) -> Any:
    """Sanitize a canary hit without exposing its shadow target name."""
    target = f"{source_collection}{CANARY_SUFFIX}"
    sanitized = sanitize_public_result(value)

    def relabel(item: Any) -> Any:
        if isinstance(item, Mapping):
            relabeled: dict[str, Any] = {}
            for key, child in item.items():
                key_text = str(key)
                if key_text in {"collection", "source_collection", "target_collection"}:
                    relabeled[key_text] = source_collection
                else:
                    relabeled[key_text] = relabel(child)
            return relabeled
        if isinstance(item, list):
            return [relabel(child) for child in item]
        if isinstance(item, tuple):
            return tuple(relabel(child) for child in item)
        if isinstance(item, str):
            return item.replace(target, source_collection)
        return item

    return relabel(sanitized)


def _api_key() -> str:
    """Read the provider key without logging or returning it to callers."""
    value = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if value:
        return value
    paths = (
        os.getenv("DEEPINFRA_KEY_FILE", ""),
        "/srv/secrets/deepinfra_api_key",
        "/run/secrets/deepinfra_api_key",
    )
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


__all__ = [
    "ALLOWED_CANARY_SOURCES",
    "CANARY_DIM",
    "CANARY_MODEL",
    "CanaryConfigError",
    "CanaryUnavailable",
    "canary_collections",
    "canary_enabled",
    "canary_target_for",
    "get_canary_query_embedding",
    "sanitize_canary_result",
    "validate_canary_vector",
]
