"""Create the ``rug_events`` collection and its payload indexes from the plan.

Kept as a script-shaped module (not a class) so it can be called from the
build pipeline, the tests, and a one-off operator run identically.

The dim-2560 default matches the rest of the corpus (Qwen3-Embedding-4B, dim
2560 per ``missioneval.golden_cases`` provenance notes); it is a parameter so a
smoke test on a scratch collection need not embed anything real.
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.rug_events.qdrant_io import QdrantError, collection_info, request
from rugbrain.rug_events.schema import COLLECTION, INDEX_PLAN

log = logging.getLogger(__name__)

#: Cosine + the Qdrant sparse "learn" setup used by the migrated *_v2 RAG
#: collections, so a future hybrid migration is a rename, not a rebuild.
DENSE_VECTOR: dict[str, Any] = {"size": 2560, "distance": "Cosine"}


def ensure_collection(base: str, name: str = COLLECTION, dry_run: bool = False) -> bool:
    """Create *name* when absent. Returns True when it now exists."""
    try:
        info = collection_info(base, name)
    except QdrantError:
        info = {}
    if info.get("status") == "green":
        return True
    if dry_run:
        log.info("ensure_collection_dry_run name=%s would_create=true", name)
        return False
    request(base, "PUT", f"/collections/{name}", {"vectors": DENSE_VECTOR})
    log.info("ensure_collection_created name=%s", name)
    return True


def apply_indexes(
    base: str,
    name: str = COLLECTION,
    plan: dict[str, dict[str, Any]] | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Create every index in the plan. Returns one result row per field.

    Safe to re-run: Qdrant treats creating an existing index as a no-op, so
    the "already indexed" case is a success, not a failure.
    """
    fields = plan if plan is not None else INDEX_PLAN
    existing = (collection_info(base, name) or {}).get("payload_schema") or {}
    results: list[dict[str, Any]] = []
    for field, params in fields.items():
        if field in existing:
            results.append({"field": field, "action": "already_indexed"})
            continue
        if not dry_run:
            request(
                base,
                "PUT",
                f"/collections/{name}/index",
                {"field_name": field, "field_schema": params},
            )
        results.append({"field": field, "action": "planned" if dry_run else "created"})
    return results


def describe(name: str = COLLECTION) -> dict[str, Any]:
    """The declared schema + index plan, for docs and the report."""
    return {"collection": name, "vector": DENSE_VECTOR, "index_plan": INDEX_PLAN}


__all__ = ["DENSE_VECTOR", "apply_indexes", "describe", "ensure_collection"]
