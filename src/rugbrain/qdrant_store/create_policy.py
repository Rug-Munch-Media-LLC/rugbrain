"""Create-time policy: may a collection be materialized at this width?

WHY THIS MODULE EXISTS
    ``_ensure_collection`` used to default ``create_if_missing=True``, so a
    collection name nobody had ever heard of was SILENTLY CREATED at whatever
    width the caller happened to hold. A typo in a collection name therefore
    forked a fresh empty corpus and the write SUCCEEDED — the exact mechanism
    that let the retired 2560 generation survive a dozen code fixes. The live
    corpus carries the scar: a collection literally named ``does_not_exist``.

    Creating is now a NAMED, EXPLICIT act (``create`` policy token) and every
    creation is width-checked against the registry's canonical lane first, so
    the retired width is structurally impossible to create rather than merely
    discouraged by a later drift scan.

THE TWO QDRANT SHAPES (both occur at 4096 on this host)
    Qdrant describes a collection's vectors either as a bare unnamed object
    ``{"size": N, "distance": ...}`` or as a named-vector mapping
    ``{"dense": {"size": N}, "bm25": {...sparse...}}`` once sparse/hybrid
    collections exist. A create-time check that only understood one of these
    silently passed on the other — an earlier attempt read only the bare shape
    and reported populated hybrid collections as having no vector config. The
    check below parses BOTH, through the one shared parser
    (:func:`collection_schema.dense_vector_dimensions`), so a reader and a
    writer can never disagree about a schema.

WIDTH IS READ FROM THE REGISTRY, NEVER HARDCODED
    The canonical width is :data:`lane_widths.TWIN_LANE_DIM` (the measured 4096
    lane). It is deliberately NOT ``4096`` written inline, and not re-derived
    from ``dim_policy.COLLECTION_DIM`` (which moves with the same
    ``RAG_EMBED_DIM`` env var as the vector it is supposed to police).

WHAT IS *NOT* REFUSED
    Creation is refused for the RETIRED width (and any non-canonical width),
    but a collection whose DECLARED width is a preserved/pinned lane is a
    separate, legitimate concept and is not this module's business: 2560 is
    still a valid physical width for pinned corpora (``rug_events``,
    ``coding_knowledge``, …, see ``lane_table.PINNED_DIMS``). This check
    governs *unknown/undeclared* names — the typo class — and never blocks a
    declared lane.
"""

from __future__ import annotations

import logging
from typing import Final

from rugbrain.qdrant_store.lane_widths import TWIN_LANE_DIM

logger = logging.getLogger(__name__)

#: The canonical (create-permitted) vector width, sourced from the registry.
#: Not a literal: see the module docstring.
CANONICAL_CREATE_DIM: Final[int] = TWIN_LANE_DIM


class RetiredWidthCreationRefused(RuntimeError):
    """A create was asked for at a width the registry does not allow.

    A distinct exception type (not a bare ``False``) so the refusal is
    IMPOSSIBLE to mistake for a Qdrant outage, a transient probe failure, or an
    incompatible *existing* schema — all of which also return False today. A
    create-time policy violation is a programming error, not a runtime state,
    and the two must not share a signal.
    """


def assert_creatable_width(collection: str, requested_width: int) -> None:
    """Raise :class:`RetiredWidthCreationRefused` unless *requested_width* is canonical.

    Called on the CREATE branch of ``_ensure_collection`` — never on the
    already-exists branch, which keeps its own configured-dims comparison so a
    correctly-populated preserved lane (2560) still serves reads and writes
    against an existing collection.
    """
    if requested_width == CANONICAL_CREATE_DIM:
        return
    raise RetiredWidthCreationRefused(
        f"qdrant_create_refused collection={collection!r} "
        f"requested_dim={requested_width} canonical_dim={CANONICAL_CREATE_DIM} "
        f"reason=non_canonical_create_width — creating at {requested_width} would "
        f"fork a retired-generation corpus. Provision the canonical "
        f"{CANONICAL_CREATE_DIM} collection, or (if a preserved/pinned lane is "
        f"genuinely intended) declare it in lane_table.PINNED_DIMS so the width "
        f"is a reviewed fact rather than an accident. Never create by typo."
    )


__all__ = [
    "CANONICAL_CREATE_DIM",
    "RetiredWidthCreationRefused",
    "assert_creatable_width",
]
