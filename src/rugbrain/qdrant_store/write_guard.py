"""Write-time vector-schema gate (rb-vec26 guard, wired into the writer).

Two refusals, both loud, both before any point is sent:

1. the vector width must match the collection's declared slot
   (``vector_schema_guard.guard_collection``);
2. the declared slot must be the canonical corpus width -- a 2560-d slot is a
   retired 4B-generation space, and accepting writes into it is exactly how
   ``scan_intel`` accumulated 20,052 points the 4096-d lane can never read.

Named/multi-slot (hybrid) collections are not judged here; slot naming belongs
to ``store_write._resolve_vector_field``.
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.vector_schema_guard import (
    SchemaMismatch,
    declared_schemas,
    guard_collection,
)

logger = logging.getLogger("rugbrain.qdrant_store.write_guard")

CANONICAL_LANE_DIM = 4096  # Qwen3-Embedding-8B; fleet corpus lane


async def slot_accepts(client: Any, collection: str, lane_dim: int) -> bool:
    """True when *collection* may receive a *lane_dim*-d vector."""
    try:
        info = await client.get_collection(collection)
        raw = info.model_dump() if hasattr(info, "model_dump") else info.__dict__
        body = {"result": raw}
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
