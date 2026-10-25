"""Write-time vector-schema gate (rb-vec26 guard, wired into the writer).

Two refusals, both loud, both before any point is sent:

1. the vector width must match the collection's declared slot
   (``vector_schema_guard.guard_collection``);
2. the declared slot must be the canonical corpus width -- a 2560-d slot is a
   retired 4B-generation space, and accepting writes into it is exactly how
   ``scan_intel`` accumulated 20,052 points the 4096-d lane can never read.

Named/multi-slot (hybrid) collections are not judged here; slot naming belongs
to ``store_write._resolve_vector_field``.

writefix46: the probe used to call ``client.get_collection()`` -- the
``qdrant-client`` Python API -- on an ``httpx.AsyncClient`` (and, in the mirrored
reproduction, a ``qdrant_client`` object reached ``client.put`` here). The store
speaks Qdrant REST; this module now does too, and the boundary is typed.
"""

from __future__ import annotations

import logging

import httpx

from rugbrain.qdrant_store.client_contract import require_qdrant_http_client
from rugbrain.vector_schema_guard import (
    SchemaMismatch,
    declared_schemas,
    guard_collection,
)

logger = logging.getLogger("rugbrain.qdrant_store.write_guard")

CANONICAL_LANE_DIM = 4096  # Qwen3-Embedding-8B; fleet corpus lane


async def slot_accepts(client: httpx.AsyncClient, collection: str, lane_dim: int) -> bool:
    """True when *collection* may receive a *lane_dim*-d vector."""
    client = require_qdrant_http_client(client, where="write_guard.slot_accepts")
    response = await client.get(f"/collections/{collection}")
    if response.status_code != 200:
        # _ensure_collection already established existence/compatibility; a
        # probe that cannot read the slot must not silently authorise a write.
        logger.warning(
            "qdrant_slot_probe_failed collection=%s status=%d", collection, response.status_code
        )
        return False
    body = {"result": response.json().get("result", {}) or {}}
    try:
        slots = declared_schemas(body)
        if len(slots) != 1:
            return True
        schema = guard_collection(body, lane_dim=lane_dim, lane_model="qdrant_store")
    except SchemaMismatch as exc:
        logger.error(
            "qdrant_insert_blocked collection=%s reason=vector_schema_mismatch detail=%s",
            collection,
            exc,
        )
        return False
    if schema.size != CANONICAL_LANE_DIM:
        logger.error(
            "qdrant_insert_blocked collection=%s reason=retired_vector_width slot=%d canonical=%d",
            collection,
            schema.size,
            CANONICAL_LANE_DIM,
        )
        return False
    return True
