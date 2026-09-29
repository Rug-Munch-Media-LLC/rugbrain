"""Compatibility facade over the collection modules. No logic lives here.

This module was the single "collection registry + gate + namespace" module and
grew past the 200-line cap. It is now a re-export of four modules that each own
ONE job, kept importable under the historical path because
``ingest_sources.registry_facts`` (outside this package) still imports
``_RMI_RAG_OWNED_COLLECTIONS`` from here, and the package ``__init__`` plus two
test modules reference the old names.

The names are re-exported, not duplicated: every symbol here IS the object
defined in its owning module, so patching the package namespace and calling
these keeps working. Do not add new names here — import the owning module
directly instead.
"""

from __future__ import annotations

from typing import Final

from rugbrain.qdrant_store.collection_lifecycle import (
    _ensure_collection,
    _existing_collections,
)
from rugbrain.qdrant_store.collection_schema import dense_vector_dimensions
from rugbrain.qdrant_store.namespace import (
    _INTERIM_SUFFIX,
    UNIFIED_COLLECTION,
    UNIFIED_READS,
    map_collection,
    unified_route,
)
from rugbrain.qdrant_store.owned_registry import (
    _RMI_RAG_OWNED_COLLECTIONS,
    _RMI_RAG_QDRANT_WRITE_MODE,
)
from rugbrain.qdrant_store.ownership import gate_collection_name

#: Historical private alias: the pre-split name of :func:`map_collection`.
_map_collection = map_collection

__all__: Final = [
    "UNIFIED_COLLECTION",
    "UNIFIED_READS",
    "_INTERIM_SUFFIX",
    "_RMI_RAG_OWNED_COLLECTIONS",
    "_RMI_RAG_QDRANT_WRITE_MODE",
    "_ensure_collection",
    "_existing_collections",
    "_map_collection",
    "dense_vector_dimensions",
    "gate_collection_name",
    "unified_route",
]
