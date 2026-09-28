"""Tests for live readiness schema validation."""

from __future__ import annotations

import respx

from rugbrain.app import health_routes
from rugbrain.app.health_state import STATE
from rugbrain.dim_policy import COLLECTION_DIM


def _collection_response(dimension: int) -> dict:
    return {
        "result": {
            "config": {
                "params": {
                    "vectors": {"size": dimension, "distance": "Cosine"},
                }
            }
        }
    }


async def test_schema_probe_accepts_canonical_dimensions() -> None:
    """The readiness probe accepts both canonical 2560-dim collections."""
    url = "http://qdrant.test"
    STATE["qdrant_url"] = url
    with respx.mock(base_url=url, assert_all_called=False) as mock:
        mock.get("/collections/news_v2").respond(200, json=_collection_response(COLLECTION_DIM))
        mock.get("/collections/rag_documents").respond(
            200, json=_collection_response(COLLECTION_DIM)
        )
        assert await health_routes.qdrant_schema_ok() is True


async def test_schema_probe_rejects_retired_dimension() -> None:
    """The readiness probe rejects a legacy 1024-dim collection."""
    url = "http://qdrant.test"
    STATE["qdrant_url"] = url
    with respx.mock(base_url=url, assert_all_called=False) as mock:
        mock.get("/collections/news_v2").respond(200, json=_collection_response(1024))
        mock.get("/collections/rag_documents").respond(
            200, json=_collection_response(COLLECTION_DIM)
        )
        assert await health_routes.qdrant_schema_ok() is False
