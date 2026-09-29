"""Collection lifecycle: does this collection exist, and can it take the vector?

Split from the registry and the gate because lifecycle is the only part of
this module family that talks to Qdrant. Everything else is a pure decision
about names. The name-parsing helper is its own tiny module because a schema
reader and the liveness probe both need it and neither should import the other's
I/O.
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.qdrant_store.collection_schema import dense_vector_dimensions

logger = logging.getLogger(__name__)

_existing_collections: dict[str, int] = {}


async def _ensure_collection(
    collection: str,
    vector_size: int,
    client: Any,
    *,
    create_if_missing: bool = True,
) -> bool:
    """Ensure a collection exists and can accept the requested vector dimension.

    Read-only callers can disable auto-creation so a missing canary target
    fails closed instead of being materialized as an empty shadow collection.
    """
    if vector_size <= 0:
        logger.error(
            "qdrant_collection_invalid_dim collection=%s requested_dim=%d",
            collection,
            vector_size,
        )
        return False
    if _existing_collections.get(collection) == vector_size:
        return True
    try:
        r = await client.get(f"/collections/{collection}")
        if r.status_code == 200:
            params = r.json().get("result", {}).get("config", {}).get("params", {}) or {}
            configured_dims = dense_vector_dimensions(params)
            if configured_dims and vector_size not in configured_dims:
                logger.error(
                    "qdrant_collection_incompatible collection=%s requested_dim=%d configured_dims=%s",
                    collection,
                    vector_size,
                    sorted(configured_dims),
                )
                return False
            _existing_collections[collection] = vector_size
            return True
        if r.status_code != 404:
            logger.error(
                "qdrant_collection_probe_failed collection=%s status=%d",
                collection,
                r.status_code,
            )
            return False
    except Exception as e:
        logger.warning("qdrant_collection_probe_failed collection=%s err=%s", collection, e)
        return False

    if not create_if_missing:
        logger.warning(
            "qdrant_collection_missing collection=%s reason=create_if_missing_false",
            collection,
        )
        return False

    try:
        payload: dict[str, Any] = {"vectors": {"size": vector_size, "distance": "Cosine"}}
        r = await client.put(f"/collections/{collection}", json=payload)
        ok = r.status_code in (200, 201)
        if ok:
            _existing_collections[collection] = vector_size
        return ok
    except Exception as e:
        logger.warning("qdrant_create_collection_failed collection=%s err=%s", collection, e)
        return False


__all__ = ["_ensure_collection", "_existing_collections"]
