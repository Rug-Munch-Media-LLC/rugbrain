"""ONE client contract for the Qdrant write path: httpx REST, nothing else.

Root cause of writefix46: ``write_guard.slot_accepts`` called
``await client.get_collection(...)`` -- the ``qdrant-client`` Python API --
while every other call in the store speaks Qdrant's REST API through an
``httpx.AsyncClient``. Both call shapes type-checked because the parameter was
annotated ``Any``, so a wrong client failed mid-write instead of at the
boundary:

    AttributeError: 'AsyncClient' object has no attribute 'get_collection'
    AttributeError: 'AsyncQdrantClient' object has no attribute 'post'

The store speaks REST. This module is the single boundary check: a
``qdrant_client`` object raises here, before a chunk is embedded or a point is
counted as an ordinary "error"/"blocked collection".
"""

from __future__ import annotations

import httpx

QDRANT_HTTP_TIMEOUT_SECONDS = 30.0

#: The only three verbs the Qdrant REST surface uses.
REQUIRED_VERBS: tuple[str, ...] = ("get", "put", "post")


class QdrantClientContractViolation(TypeError):
    """A non-REST client reached a Qdrant REST-only boundary."""

    code = "qdrant_client_contract_violation"


def require_qdrant_http_client(client: object, *, where: str) -> httpx.AsyncClient:
    """Return *client* typed as ``httpx.AsyncClient``, or raise immediately."""
    missing = [verb for verb in REQUIRED_VERBS if not callable(getattr(client, verb, None))]
    if isinstance(client, httpx.Client) and not isinstance(client, httpx.AsyncClient):
        missing.append("async_client")
    if missing:
        raise QdrantClientContractViolation(
            f"{QdrantClientContractViolation.code} where={where} got={type(client).__name__} "
            f"missing={missing} -- the Qdrant write path speaks REST through "
            "httpx.AsyncClient; a qdrant_client.AsyncQdrantClient must be adapted at "
            "the caller, never passed through"
        )
    return client


__all__ = [
    "QDRANT_HTTP_TIMEOUT_SECONDS",
    "QdrantClientContractViolation",
    "require_qdrant_http_client",
]
