"""Dimension-drift guard — the check that makes silent zero-results impossible.

The failure this module exists to stop (2026-09-28): a query embedded at
4096 dims was sent to a 2560-dim Qdrant collection. Qdrant rejects the
request shape, the caller sees an EMPTY hit list, and an infrastructure
mismatch is indistinguishable from a corpus that genuinely cannot answer.
Same for a configured ``RAG_EMBED_DIM`` that disagrees with what the live
embedder actually returns.

Two invariants, both checked before a vector ever reaches the store:

1. ``configured dim`` (RAG_EMBED_DIM / the embedder's own contract) ==
   ``observed dim`` (length of the vector the embedder really produced).
2. ``observed dim`` == ``collection dim`` for the collection being searched.

On either failure this raises :class:`EmbeddingDimMismatch` naming BOTH
sides of the mismatch. It never returns an empty result set: an empty
result is a legitimate answer, a wrong-dimension query is not.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Final

from rugbrain.dim_policy import COLLECTION_DIM
from rugbrain.search.vector_shapes import read_vector_dims, resolve_requested_dim

log = logging.getLogger(__name__)


#: Configured query dim. Read at call time (not import time) so a test or an
#: operator override is honoured without reimporting the process.
def configured_dim() -> int:
    """The dim the fleet is CONFIGURED to produce (``RAG_EMBED_DIM``)."""
    raw = os.getenv("RAG_EMBED_DIM")
    if raw:
        return int(raw)
    return COLLECTION_DIM


class EmbeddingDimMismatch(RuntimeError):
    """A query vector cannot be searched against the target index.

    Carries the three dims involved so a caller/test/metric can assert on
    the mismatch instead of inferring it from an empty result set.
    """

    def __init__(self, message: str, *, expected: int, actual: int, where: str = "") -> None:
        self.expected = expected
        self.actual = actual
        self.where = where
        super().__init__(message)


def _collection_dims(params: Any) -> dict[str | None, int]:
    """Normalise Qdrant vector params to ``{named_vector | None: size}``.

    Delegates to :mod:`rugbrain.search.vector_shapes`, which owns BOTH shapes
    (bare ``{"size": N}`` and named ``{"dense": {"size": N}}``). This module
    used to carry its own copy and read only the named one, so a live bare
    collection such as ``threat_intel_v2_8b`` parsed as "no vector config" and
    raised ``UnknownCollection`` — a false report about a healthy 4096
    collection. Do not re-inline the parsing here; the single parser is the fix.
    """
    return read_vector_dims(params)


async def assert_collection_dim(
    qdrant: Any,
    collection: str,
    vector_name: str | None = None,
) -> int:
    """Assert the live collection's vector dim matches ``configured_dim()``.

    Returns the confirmed dim. Raises :class:`EmbeddingDimMismatch` when the
    store disagrees with the configuration — the 4096-vs-2560 case.
    A missing collection is a different fault and is reported as such
    (:class:`UnknownCollection`) rather than as a dim mismatch.
    """
    want = configured_dim()
    try:
        info = await qdrant.get_collection(collection)
    except Exception as exc:
        raise UnknownCollection(collection, exc) from exc

    dims = _collection_dims(info.config.params)
    if not dims:
        raise UnknownCollection(collection, RuntimeError("collection exposes no vector config"))

    present = resolve_requested_dim(dims, vector_name)
    if present is None:
        raise UnknownCollection(
            collection,
            f"cannot resolve vector dim (requested={vector_name!r} names={sorted(map(str, dims))})",
        )
    if present != want:
        raise EmbeddingDimMismatch(
            f"collection_dim_mismatch collection={collection} vector={vector_name or 'default'} "
            f"store={present} configured={want} — fix RAG_EMBED_DIM or reindex, never ignore",
            expected=want,
            actual=present,
            where=collection,
        )
    return present


class UnknownCollection(RuntimeError):
    """The requested collection (or its named vector) does not exist."""


def assert_query_dim(vector: list[float], *, where: str = "query") -> int:
    """Assert a live query vector matches the configured dim.

    The embedder's own ``_resize`` already rejects a mismatch, but that gate
    is per-backend. This is the pre-Qdrant gate that holds regardless of
    which lane produced the vector.
    """
    want = configured_dim()
    got = len(vector)
    if got != want:
        raise EmbeddingDimMismatch(
            f"query_dim_mismatch scope={where} embedder={got} configured={want} — "
            "the embedder is serving a different model than the corpus; "
            "refusing to search (an empty result here would be a lie)",
            expected=want,
            actual=got,
            where=where,
        )
    return got


#: Process-local memo of confirmed collection dims, keyed by collection.
#: Avoids a get_collection round-trip per query while staying correct: a
#: schema change is a redeploy, and ``RAG_DIM_GUARD_NO_CACHE=1`` disables it.
_CACHE: dict[str, int] = {}
_CACHE_TTL_S: Final = 300.0
_CACHE_STAMP: dict[str, float] = {}


async def guard_query_dim(
    qdrant: Any,
    collection: str,
    vector: list[float],
    vector_name: str | None = None,
) -> int:
    """Full pre-search gate: query dim AND collection dim, both asserted.

    Order matters: the query dim is checked first because it is the cheaper
    check and the more common drift (an embedder serving a different model).
    """
    import time

    if os.getenv("RAG_DIM_GUARD_NO_CACHE", "").lower() in ("1", "true", "yes"):
        assert_query_dim(vector, where=collection)
        return await assert_collection_dim(qdrant, collection, vector_name)

    hit = _CACHE.get(collection)
    fresh = hit is not None and (time.monotonic() - _CACHE_STAMP[collection]) < _CACHE_TTL_S
    assert_query_dim(vector, where=collection)
    if fresh:
        return hit
    confirmed = await assert_collection_dim(qdrant, collection, vector_name)
    _CACHE[collection] = confirmed
    _CACHE_STAMP[collection] = time.monotonic()
    return confirmed


def clear_cache() -> None:
    """Drop the confirmed-dim memo (tests, and after a schema change)."""
    _CACHE.clear()
    _CACHE_STAMP.clear()


__all__ = [
    "EmbeddingDimMismatch",
    "UnknownCollection",
    "assert_collection_dim",
    "assert_query_dim",
    "clear_cache",
    "configured_dim",
    "guard_query_dim",
]
