"""Read a vector schema out of a Qdrant collection config.

Pure parsing, no I/O — kept apart from ``collection_lifecycle`` (which probes
Qdrant) and the read paths that consume it, so a reader and a writer can never
disagree about a schema because they parsed it differently.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def dense_vector_dimensions(params: Mapping[str, Any]) -> set[int]:
    """Return configured dense vector dimensions from a Qdrant params object.

    Qdrant uses either a legacy bare-vector object (``{"size": N}``) or a
    named-vector object (``{"dense": {"size": N}}``).  Keeping this parsing in
    one place prevents readers and writers from disagreeing about a schema.
    """
    vectors = params.get("vectors")
    if not isinstance(vectors, dict):
        return set()
    size = vectors.get("size")
    if isinstance(size, int):
        return {size}
    dimensions: set[int] = set()
    for spec in vectors.values():
        if isinstance(spec, dict) and isinstance(spec.get("size"), int):
            dimensions.add(int(spec["size"]))
    return dimensions


__all__ = ["dense_vector_dimensions"]
