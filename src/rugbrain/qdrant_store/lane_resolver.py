"""Canonical-lane collection resolver: base name -> its ``_8b`` twin.

The 8B knowledge migration is COMPLETE. Every ``*_8b`` twin exists at 4096 and
is populated. What was left broken is the NAME: every ingest source's
``DEFAULT_COLLECTION`` still declared the 2560 base, so the canonical lane
produced a 4096 vector at a 2560 name, the dimension guard correctly refused,
and the run exited non-zero. The migration was never the problem.

THE RULE
    A base name is rewritten to ``<name>_8b`` IFF this repo's declared lane for
    that name is the canonical dim (:data:`CANONICAL_LANE_DIM`, 4096) AND a twin
    name is declared for it. Anything else passes through unchanged:

      * ``threat_intel_v2``    -> ``threat_intel_v2_8b``   (INFO logged)
      * ``threat_intel_v2_8b`` -> ``threat_intel_v2_8b``   (idempotent pass)
      * ``privacy_protocol``   -> ``privacy_protocol``     (already 4096, no twin)

Never a silent rewrite: every rewrite logs ``base`` and ``resolved`` at INFO.

AN UNKNOWN NAME IS NOT A LANE ERROR
    A name absent from the declared table and not already canonical passes
    through UNCHANGED, and this is deliberate. It is NOT silently swallowed:
    :func:`declared_lane_dim` returns ``None`` for it, and the dimension guard
    is the backstop. The important caveat is that for a *genuinely* unknown
    name the guard does not refuse — ``_ensure_collection`` defaults to
    ``create_if_missing=True`` and a fresh 4096 collection is created and
    written. So "the guard will catch it" is true for the two CLASSIFIED
    no-twin groups (already-4096 names pass cleanly, pinned names are refused
    loudly) and FALSE for a typo: a typo silently forks a new empty corpus.

    :class:`UnresolvedLane` is retained for the failure it was built for and is
    raised ONLY by :func:`require_resolved`, an explicit opt-in assertion for
    callers that already KNOW their name must resolve. ``resolve_collection``
    itself does not raise: making it raise would reject every name outside the
    owned set, including read-only corpora this repo does not write.

WHY A DECLARED TABLE, NOT "DOES THE TWIN EXIST?"
    Live Qdrant (96 collections, 33 twins) proves the two are not the same
    predicate. 21 owned names are migrated (2560 base + 4096 twin) and need the
    rewrite. 11 owned names are ALREADY canonical at 4096 and never had a 2560
    base, so a twin-absence test would leave them alone only by luck. 6 more are
    pinned to 2560 with no twin. Keying on the live dim instead makes the
    resolver depend on Qdrant being up and on a network call per write, and
    makes the write target change under the operator's feet. The table is a
    declaration like ``DEFAULT_COLLECTION`` was — but a checked one, holding
    exactly one fact per name that the defect duplicated in two places.

The reverse direction (given a live name, report its dim) is in ``lane_dims``.
"""

from __future__ import annotations

import logging
import os
from typing import Final

# `fetch_live_dim` lives in `lane_dims` (it reads the dimension off a live
# Qdrant collection) and the declared facts live in `lane_table`. Both are
# re-exported here so this module stays the single public surface of the lane:
# `qdrant_store/__init__` and callers resolve the resolver, and splitting the
# module without carrying the names forward left the package unimportable.
from rugbrain.qdrant_store.lane_dims import fetch_live_dim
from rugbrain.qdrant_store.lane_table import (
    CANONICAL_LANE_DIM,
)
from rugbrain.qdrant_store.lane_table import (
    DECLARED_TWINS as _DECLARED_TWINS,
)
from rugbrain.qdrant_store.lane_table import (
    PINNED_DIMS as _PINNED_DIMS,
)

logger = logging.getLogger(__name__)

#: Suffix of a migrated twin. Shared with ``canary_routing.CANARY_SUFFIX``.
EIGHT_B_SUFFIX: Final = "_8b"

#: Retired 2560 generation (qwen3-embedding-4b). Read-only, never a write target.
RETIRED_LANE_DIM: Final = 2560

#: Escape hatch. Off by default; set ``RAG_LANE_RESOLVER=off`` to restore the
#: pre-resolver behaviour for a single run. Values: off | warn | strict.
#: ``warn`` logs and falls through; ``strict`` is the default (fail loud).
RESOLVER_MODE_ENV: Final = "RAG_LANE_RESOLVER"
RESOLVER_MODES: Final = frozenset({"off", "warn", "strict"})


def _lane_twin(name: str) -> str:
    return f"{name}{EIGHT_B_SUFFIX}"


def resolver_mode() -> str:
    """Resolve the effective mode, defaulting to ``strict`` when unset/invalid."""
    raw = os.getenv(RESOLVER_MODE_ENV, "strict").strip().lower()
    return raw if raw in RESOLVER_MODES else "strict"


def declared_lane_dim(collection: str | None) -> int | None:
    """The dim this repo writes *collection* at, or None when unclassified.

    Accepts a base name OR a resolved ``*_8b`` twin, so a caller can ask about
    the name it just got back from :func:`resolve_collection`. None means "no
    declared fact" — the caller decides, and for writes that is
    :data:`CANONICAL_LANE_DIM` (the 4096 default everywhere in the repo).
    """
    if not collection:
        return None
    if collection in _DECLARED_TWINS:
        return CANONICAL_LANE_DIM
    base = strip_twin_suffix(collection)
    if base in _DECLARED_TWINS:
        return CANONICAL_LANE_DIM
    return _PINNED_DIMS.get(collection) or _PINNED_DIMS.get(base or "")


def resolve_collection(collection: str | None) -> str | None:
    """Resolve a declared collection name to the name writes should target.

    Idempotent: resolving an already-canonical ``*_8b`` name returns it
    unchanged, so a re-run never appends a second suffix. ``None`` and ``""``
    pass through — the write path turns an empty name into ``"default"``.
    """
    if not collection:
        return collection
    mode = resolver_mode()
    if mode == "off" or collection.endswith(EIGHT_B_SUFFIX):
        return collection
    if collection not in _DECLARED_TWINS:
        return collection
    resolved = _lane_twin(collection)
    if mode == "warn":
        logger.warning(
            "qdrant_lane_resolve base=%s resolved=%s mode=warn — strict would resolve here",
            collection,
            resolved,
        )
        return resolved
    logger.info(
        "qdrant_lane_resolve base=%s resolved=%s dim=%d reason=canonical_lane_8b_twin",
        collection,
        resolved,
        CANONICAL_LANE_DIM,
    )
    return resolved


def strip_twin_suffix(collection: str | None) -> str | None:
    """Return the base name behind a twin, for gate evaluation.

    Ownership is checked on the BASE name: the ``_8b`` suffix is a lane
    marker, not a different owner. This coexists with the interim-suffix strip
    in ``store_write.insert`` — call it on the result of
    :func:`rugbrain.qdrant_store.collections._map_collection`, which appends the
    interim suffix outermost.
    """
    if collection and collection.endswith(EIGHT_B_SUFFIX):
        return collection[: -len(EIGHT_B_SUFFIX)]
    return collection


# ``UnresolvedLane`` and ``require_resolved`` live in ``lane_strict``, which
# imports THIS module, so the re-export is at the BOTTOM on purpose: at the top
# it would be a circular import, because this module imports ``lane_dims``, the
# package ``__init__`` imports ``collections``, and ``collections`` imports this
# module. Importing the sibling only after every name above is defined breaks
# the cycle while keeping one public surface. These are the SAME class object,
# not a subclass, so ``except UnresolvedLane`` still catches a raise.
from rugbrain.qdrant_store.lane_strict import (  # noqa: E402
    UnresolvedLane as UnresolvedLane,
)
from rugbrain.qdrant_store.lane_strict import (  # noqa: E402
    require_resolved as require_resolved,
)

__all__ = [  # noqa: RUF022 -- kept alphabetical for review
    "CANONICAL_LANE_DIM",
    "EIGHT_B_SUFFIX",
    "RESOLVER_MODES",
    "RESOLVER_MODE_ENV",
    "RETIRED_LANE_DIM",
    "UnresolvedLane",
    "declared_lane_dim",
    "fetch_live_dim",
    "require_resolved",
    "resolver_mode",
    "resolve_collection",
    "strip_twin_suffix",
]
