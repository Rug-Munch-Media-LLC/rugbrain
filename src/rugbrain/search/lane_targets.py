"""Search-side lane resolution: the seam every read must pass through.

THE DEFECT THIS EXISTS TO STOP
    The WRITE path is routed through
    :func:`rugbrain.qdrant_store.lane_resolver.resolve_collection`, so a base
    collection name resolves to its ``_8b`` twin. The SEARCH path was not, and
    not by accident — it re-derived names in three independent places:

      1. :func:`rugbrain.search.engine.query_intent.resolve_search_collections`
         returned base names, so every orchestrated search named a 2560
         collection while the serving lane had written 4096.
      2. ``qdrant_store.collections.unified_route`` maps a consolidated source
         onto ``UNIFIED_COLLECTION`` (``rag_documents``) and returns that
         constant RAW. The ``_map_collection`` leg of the same function DOES
         resolve, so the base name and the consolidated route disagreed with
         each other inside one function. Live proof (RAG_UNIFIED_READS=1):
         ``unified_route('news_articles') -> 'rag_documents'`` at 2560 dims,
         which is the ``qdrant_hybrid_search_blocked collection=rag_documents
         requested_dim=4096 reason=incompatible_schema`` in the incident.

    The failure was invisible because it degraded to an empty result set —
    identical to "the corpus has no answer" — and because the dimension guard
    that could have caught it was itself reading the wrong shape (see
    :func:`read_vector_dim`).

THE RULE
    A component that decides which collections are in play MUST resolve through
    the same resolver as the component that decides which collections are
    written. There is no second opinion. :func:`resolve_search_collection` is a
    thin, honest wrapper over Lane Q's resolver — it adds no table, no suffix,
    and no fallback of its own, precisely so the two sides cannot drift again.

WHY NOT FIX ``unified_route`` ITSELF
    ``collections.py`` is Lane Q's file. Lane P may call it, never edit it.
    Resolving BEFORE the call fixes the read path without touching the write
    path, and :func:`resolve_search_collection` is idempotent, so a future
    Lane Q fix to ``unified_route`` composes rather than double-appends.

WHAT THIS DOES NOT COVER (see also ``search/dim_guard.py``)
    Redis content keys (``rag:{collection}:{doc_id}``) are written by the
    ingest path under the BASE name and are NOT rewritten by the resolver.
    :func:`redis_content_collection` therefore returns the base name
    deliberately — resolving it would silently empty the keyword/BM25 lane
    rather than raise. Read the docstring on that function before touching it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from rugbrain.qdrant_store.lane_resolver import (
    CANONICAL_LANE_DIM,
    resolve_collection,
    strip_twin_suffix,
)

logger = logging.getLogger(__name__)

#: A collection name that survived resolution without being a declared base.
_UNCLASSIFIED = object()


def resolve_search_collection(collection: str | None) -> str | None:
    """Resolve a collection name to the one the serving lane actually populates.

    Thin pass-through to :func:`rugbrain.qdrant_store.lane_resolver.resolve_collection`.
    Deliberately not a re-implementation: a second table here would be the same
    defect one layer up. Idempotent, so it is safe to call on a name that has
    already been resolved, and safe to call before ``unified_route`` as well as
    after it.
    """
    return resolve_collection(collection)


def resolve_search_collections(
    collections: Iterable[str] | None,
) -> list[str]:
    """Resolve a whole collection list, preserving order and dropping duplicates.

    Order matters: the orchestrators treat the list as a priority order
    (CODING_COLLECTIONS before SCAM_COLLECTIONS), and a set would destroy that.
    Duplicates are dropped because a name and its twin are the same corpus —
    searching both would double-count the same rows in the RRF fusion.
    """
    seen: set[str] = set()
    out: list[str] = []
    for name in collections or ():
        resolved = resolve_search_collection(name) or name
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def redis_content_collection(collection: str | None) -> str | None:
    """Return the name to use for ``rag:{collection}:{doc_id}`` Redis content keys.

    The BASE name, always. Redis content keys are written by the ingest path
    under the name that source declared, which is the base; the lane resolver
    only renames the Qdrant collection, not the content cache. Resolving here
    would make the keyword lane and the ANN enrichment look up
    ``rag:threat_intel_v2_8b:<id>``, find nothing, and return rows with empty
    bodies — a silent-degradation failure of exactly the class this module
    exists to remove. Qdrant names are lane-scoped; Redis content keys are not.
    """

    return strip_twin_suffix(collection) if collection else collection


def lane_report(collection: str | None) -> dict[str, object]:
    """Describe how one name resolves. Diagnostics only — never a decision path.

    Returns the base name, the resolved name and the declared dim, so a log
    line or a test can state the mapping instead of asserting it.
    """
    from rugbrain.qdrant_store.lane_resolver import declared_lane_dim

    base = strip_twin_suffix(collection)
    return {
        "base": base,
        "resolved": resolve_search_collection(collection),
        "declared_dim": declared_lane_dim(collection),
        "canonical_dim": CANONICAL_LANE_DIM,
    }


__all__ = [
    "CANONICAL_LANE_DIM",
    "lane_report",
    "redis_content_collection",
    "resolve_search_collection",
    "resolve_search_collections",
]
