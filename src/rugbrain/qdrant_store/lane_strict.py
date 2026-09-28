"""The strict assertion over the canonical lane: resolve, or raise.

Split from ``lane_resolver`` so the resolver stays under the 200-line cap and so
the FAIL-LOUD contract lives in one readable place. It depends on
``lane_resolver``; nothing in ``lane_resolver`` depends on it, so the import
graph stays acyclic and a caller can take either half.

WHY THIS EXISTS
    ``resolve_collection`` is deliberately total: it never raises, because an
    undeclared name is not a lane error (see its docstring). That makes the
    total function WRONG for a caller who already knows the name is a declared
    write source — there, a pass-through means the canonical lane was bypassed
    and must be loud. :func:`require_resolved` is that strict counterpart: same
    resolution, plus the refusal.

    The split is the point. A single function that both rewrote and raised
    would have to either reject read-only corpora this repo does not own, or
    silently pass the write sources through. Neither is acceptable, so the
    guarantee is opt-in at the call site instead of ambient in the resolver.
"""

from __future__ import annotations

from typing import NoReturn

from rugbrain.qdrant_store.lane_resolver import (
    CANONICAL_LANE_DIM,
    declared_lane_dim,
    resolve_collection,
)


class UnresolvedLane(ValueError):
    """A name that MUST resolve to a twin did not.

    A :class:`ValueError` so a caller can catch it alongside the other
    value-shaped lane failures rather than wrapping the write path in a bare
    ``except Exception``.
    """


def _fail(collection: str | None, resolved: str | None) -> NoReturn:
    raise UnresolvedLane(
        f"collection={collection!r} resolved={resolved!r} reason=no_canonical_lane_twin"
    )


def require_resolved(collection: str) -> str:
    """Resolve *collection* or raise :class:`UnresolvedLane`.

    The strict counterpart of :func:`resolve_collection`, for callers that know
    their name is a declared write source. A name that resolves is returned; a
    name with no canonical twin, or an empty name, is refused. Empty is refused
    because a write path that has lost its ``DEFAULT_COLLECTION`` would
    otherwise create an empty collection called ``"default"`` and bury the
    mistake.

    Note the pinned and already-4096 names (``privacy_protocol``,
    ``rug_events``, …) do NOT raise: they are classified, not
    canonical-resolvable, and refusing them would break working sources. The
    assertion is "this name is on the canonical lane", not "this name has a
    twin".

    Raises:
        UnresolvedLane: *collection* is empty, or resolves off the canonical
            lane.
    """
    resolved = resolve_collection(collection)
    if not resolved or declared_lane_dim(resolved) != CANONICAL_LANE_DIM:
        _fail(collection, resolved)
    return resolved


__all__ = ["UnresolvedLane", "require_resolved"]
