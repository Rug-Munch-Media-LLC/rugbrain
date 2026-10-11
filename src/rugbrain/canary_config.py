"""Canary constants, the allowlist, and the config gate.

This module owns ONE job: deciding whether a collection may be served by the
8B canary lane, and under what name. It never embeds, never talks to a
provider, and never sanitises a hit.

The canonical RugBrain path remains Qwen3-Embedding-8B at 2560 dimensions.
The canary is default-off and read-only; enabling it with an incomplete,
internal or unknown collection is a configuration error — never a silent
fallback to a different vector space.
"""

from __future__ import annotations

import os

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
        # 2026-09-28: scam_pattern_signatures_8b is deliberately NOT here. It was
        # added in 721c451 and broke test_canary_routing, because
        # ALLOWED_CANARY_SOURCES must be a subset of PUBLIC_COLLECTIONS and that
        # collection is not publicly exposed. Adding it to the public contract is
        # a decision, not a registration: it would expose the aggregated pattern
        # set to the read path, and the collection is still empty. It stays
        # owned (write gate) and it stays unregistered for canary. Promote it by
        # adding it to PUBLIC_COLLECTIONS first, then here.
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


def canary_collections() -> frozenset[str]:
    """Return configured canary sources after validating each one."""
    if not _enabled():
        return frozenset()
    configured = _configured_sources()
    invalid = configured - ALLOWED_CANARY_SOURCES
    if invalid:
        raise CanaryConfigError("one or more canary collections are not allowlisted")
    return configured


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


__all__ = [
    "ALLOWED_CANARY_SOURCES",
    "CANARY_DIM",
    "CANARY_MODEL",
    "CANARY_SUFFIX",
    "CanaryConfigError",
    "CanaryUnavailable",
    "canary_collections",
    "canary_enabled",
    "canary_target_for",
]
