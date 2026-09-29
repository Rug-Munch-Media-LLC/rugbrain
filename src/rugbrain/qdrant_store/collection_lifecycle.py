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
from rugbrain.qdrant_store.create_policy import (
    RetiredWidthCreationRefused,
    assert_creatable_width,
)

logger = logging.getLogger(__name__)

_existing_collections: dict[str, int] = {}

#: Sentinel proving the caller NAMED creation, as opposed to omitting the
#: argument and inheriting the old fail-open default. A bool cannot express
#: "I did not choose"; this can, and that difference is the whole fix.
CREATE_IF_MISSING_DEFAULT = "refuse"


async def _ensure_collection(
    collection: str,
    vector_size: int,
    client: Any,
    *,
    create_if_missing: bool | str = CREATE_IF_MISSING_DEFAULT,
) -> bool:
    """Ensure a collection exists and can accept the requested vector dimension.

    CREATION IS FAIL-CLOSED BY DEFAULT
        The default is the sentinel :data:`CREATE_IF_MISSING_DEFAULT`
        ("refuse"), not ``True``. The old default was ``True``, so a genuinely
        unknown collection name did not trip the dimension guard — it was
        CREATED at the caller's width and the write SUCCEEDED. That is how a
        typo in a collection name forked a fresh empty corpus which then
        diverged forever, silently. A caller must now pass
        ``create_if_missing=True`` **explicitly and by name** to opt in to
        materializing a new collection.

    CREATION IS WIDTH-CHECKED
        Even an explicit ``create_if_missing=True`` is refused by
        :func:`assert_creatable_width` unless the width is the registry's
        canonical lane, so the retired generation cannot be created even by a
        caller that deliberately asked to create. The refusal is a distinct
        exception (:class:`RetiredWidthCreationRefused`), never a bare False,
        so it can never be misread as a transient Qdrant failure.

    Read-only callers can set ``create_if_missing=False`` (or leave the
    default) so a missing canary target fails closed instead of being
    materialized as an empty shadow collection.
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

    if create_if_missing is False:
        logger.warning(
            "qdrant_collection_missing collection=%s reason=create_if_missing_false",
            collection,
        )
        return False

    # Anything other than an explicit True is the fail-closed default or an
    # explicit refusal. Guard against a truthy non-True leaking through.
    if create_if_missing is not True:
        logger.warning(
            "qdrant_collection_missing collection=%s reason=create_not_named "
            "(pass create_if_missing=True to opt in)",
            collection,
        )
        return False

    # Explicit, named opt-in reached. The width still has to be canonical —
    # this raises rather than creating a retired-width fork.
    assert_creatable_width(collection, vector_size)

    try:
        payload: dict[str, Any] = {"vectors": {"size": vector_size, "distance": "Cosine"}}
        r = await client.put(f"/collections/{collection}", json=payload)
        ok = r.status_code in (200, 201)
        if ok:
            _existing_collections[collection] = vector_size
        return ok
    except RetiredWidthCreationRefused:
        raise
    except Exception as e:
        logger.warning("qdrant_create_collection_failed collection=%s err=%s", collection, e)
        return False


__all__ = ["_ensure_collection", "_existing_collections"]
