"""Reverse direction: report the dim of a LIVE Qdrant collection.

The forward question ("which collection should this name write to?") is
answered declaratively by ``lane_resolver`` with no network at all. This module
answers the opposite one — "what does the server actually have?" — which is
what the corpus manifest and the verifier need to check a DECLARED dim against
the LIVE one rather than against a hardcoded constant.

Kept separate from ``lane_resolver`` for two reasons: the two questions have
opposite trust properties (a local table vs. a live probe that can be stale,
down or ambiguous), and neither module stays under the 200-line cap if it
carries both.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "fetch_live_dim",
]


async def fetch_live_dim(
    collection: str,
    client: httpx.AsyncClient,
    *,
    probe_timeout: float = 10.0,
) -> int | None:
    """The dim of a LIVE Qdrant collection, or None when it does not exist.

    The reverse of :func:`resolve_collection`: given a name, report what the
    server actually has, so the corpus manifest and the verifier can compare a
    DECLARED dim against the LIVE one instead of against a hardcoded constant.

    Named-vector collections return the dense dimension. A collection whose
    config declares several different dimensions returns None — there is no
    single honest answer and the caller must not be handed one.
    """
    from rugbrain.qdrant_store.collections import dense_vector_dimensions

    try:
        response = await client.get(
            f"/collections/{collection}",
            timeout=probe_timeout,
        )
    except Exception as exc:  # network/HTTP layer — caller decides severity
        logger.warning("qdrant_live_dim_probe_failed collection=%s err=%s", collection, exc)
        return None
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        logger.warning(
            "qdrant_live_dim_probe_failed collection=%s status=%d",
            collection,
            response.status_code,
        )
        return None
    try:
        result = response.json().get("result", {}) or {}
        params = result.get("config", {}).get("params", {}) or {}
    except Exception as exc:
        logger.warning("qdrant_live_dim_parse_failed collection=%s err=%s", collection, exc)
        return None
    dimensions = dense_vector_dimensions(params)
    if len(dimensions) == 1:
        return dimensions.pop()
    if not dimensions:
        logger.warning("qdrant_live_dim_absent collection=%s", collection)
        return None
    logger.warning(
        "qdrant_live_dim_ambiguous collection=%s dims=%s",
        collection,
        sorted(dimensions),
    )
    return None
