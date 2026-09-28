"""Audience boundary for RugBrain's public crypto-intelligence surface.

ContextCrypto remains the owner of internal code, routing, and engineering
knowledge. RugBrain may expose only explicitly public collections and
sanitized results. This module is the fail-closed policy boundary used by
HTTP routes and search enrichment; it intentionally contains no retrieval or
LLM logic.
"""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from rugbrain.knowledge_boundary_tables import (
    FORBIDDEN_PUBLIC_KEYS,
    INTERNAL_COLLECTIONS,
    PUBLIC_COLLECTIONS,
)

#: Private alias kept for the sanitiser's internal lookup and for callers that
#: imported the underscore name before the tables moved to their own module.
_FORBIDDEN_PUBLIC_KEYS = FORBIDDEN_PUBLIC_KEYS


class Audience(StrEnum):
    """Supported retrieval audiences."""

    PUBLIC = "public"
    INTERNAL = "internal"


# These are product-owned crypto-intelligence collections. The list is an
# allowlist, not a denylist: a newly-created collection is private until an
# owner explicitly adds it here after provenance and leakage review.

# ContextCrypto and fleet-internal collections are never accepted by public
# routes. Prefixes cover future internal names without relying on a growing
# denylist. ``rag_documents`` is intentionally absent: it is an implementation
# consolidation target, not a public contract.

# Fields that expose implementation details rather than public intelligence.
# Keep this broad: public provenance and generic crypto metadata are allowed;
# internal paths, model/provider identity, prompts, raw tool payloads, and
# source-graph coordinates are not.

_INTERNAL_TOKEN_RE = re.compile(
    r"(?ix)"
    r"(?:\b(?:qwen(?:/qwen3)?(?:[- ]embedding|-reranker)?(?:-\d+b)?|qwen3(?:[- ]embedding|-reranker)?(?:-\d+b)?|"
    r"deepinfra|deepseek(?:-v\d+)?|"
    r"llama\.cpp|qdrant|b2|contextcrypto|opencode-zen|master_skills|dispatch_brief|"
    r"rugmunch|rmi-[a-z]+|talos|cinnabox|netcup)\b"
    r"|(?:/srv/|/root/|/home/dev/)[^\s\"'`]+)"
)
_SECRET_RE = re.compile(r"\b(?:sk|pk|api|key)[-_][A-Za-z0-9_-]{12,}\b", re.IGNORECASE)
_IP_RE = re.compile(r"\b(?:10|100|172|192)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


class CollectionNotPublic(PermissionError):
    """Raised when a public route is asked for a non-public collection."""


class InternalCodeAccessDenied(PermissionError):
    """Raised when a raw internal code retrieval is attempted publicly."""


def require_public_collection(collection: str) -> str:
    """Return a normalized public collection or fail without echoing it.

    The exception intentionally has a generic message. Public API errors must
    not disclose the existence or names of internal collections.
    """
    normalized = str(collection or "").strip().lower()
    if normalized not in PUBLIC_COLLECTIONS:
        raise CollectionNotPublic("collection is not available to this audience")
    return normalized


def is_internal_collection(collection: str) -> bool:
    """Whether a collection belongs to the internal ContextCrypto/fleet side."""
    normalized = str(collection or "").strip().lower()
    return (
        normalized in INTERNAL_COLLECTIONS
        or normalized.startswith("internal/")
        or normalized.startswith("contextcrypto")
    )


def _redact_text(value: str) -> str:
    """Remove internal implementation tokens and secret-shaped values."""
    value = _INTERNAL_TOKEN_RE.sub("[internal implementation detail]", value)
    value = _SECRET_RE.sub("[redacted credential]", value)
    return _IP_RE.sub("[internal address]", value)


def sanitize_public_result(value: Any) -> Any:
    """Recursively copy a result into a public-safe representation.

    The function does not mutate the retrieved point. Unknown scalar values
    are retained; only known implementation-bearing fields and text tokens
    are removed. This gives us forward-compatible payload schemas without
    allowing an unreviewed key to become an accidental public channel.
    """
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key.lower() in _FORBIDDEN_PUBLIC_KEYS:
                continue
            sanitized[key] = sanitize_public_result(raw_value)
        return sanitized
    if isinstance(value, list):
        return [sanitize_public_result(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_public_result(item) for item in value)
    if isinstance(value, str):
        return _redact_text(value)
    return value


def internal_code_access_allowed(headers: Mapping[str, str]) -> bool:
    """Check an explicit, opt-in credential for the internal code route.

    Raw ContextCrypto code retrieval is never enabled by default. An operator
    must set ``RUGBRAIN_INTERNAL_CODE_KEY`` and send the matching header.
    This is a second boundary beside the audience/collection policy; the
    public API key is intentionally not accepted here.
    """
    expected = os.getenv("RUGBRAIN_INTERNAL_CODE_KEY", "")
    supplied = headers.get("x-internal-code-key", "")
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


__all__ = [
    "INTERNAL_COLLECTIONS",
    "PUBLIC_COLLECTIONS",
    "Audience",
    "CollectionNotPublic",
    "InternalCodeAccessDenied",
    "internal_code_access_allowed",
    "is_internal_collection",
    "require_public_collection",
    "sanitize_public_result",
]
