"""Qdrant vector-config shape parsing — the reader half of the dim guard.

WHY A SEPARATE MODULE
    ``dim_guard`` enforces two invariants (query dim == configured dim, and
    configured dim == collection dim). Parsing the collection's vector config
    is a THIRD job: it decides what the store actually looks like. It grew past
    the 200-line cap precisely because a 20-line shape reader was carrying a
    40-line essay about why the shapes differ. Split it, and both files shrink.

THE TWO SHAPES, AND WHY READING ONE IS A LIE
    Qdrant returns either

      * a **bare** single vector  ``VectorParams(size=4096)``  -> ``{None: 4096}``
      * a **named** vector map   ``{"dense": VectorParams(...)}`` -> ``{"dense": 4096}``

    and both are live, side by side on the same host at the same dim: measured
    against ``http://rmi-qdrant-v2:6334`` on 2026-09-28,
    ``rag_documents_8b`` is NAMED and ``threat_intel_v2_8b`` is BARE, both 4096.
    A reader that only understands the named shape sees ``{}`` for the bare one
    and reports ``UnknownCollection("collection exposes no vector config")`` —
    a healthy, fully populated collection declared missing, which is the worst
    possible failure: a loud, confident, FALSE dimension report.

    The REST client yields dicts; the typed client yields ``VectorParams``. So
    each spec is read as object-first, mapping-second. Do not collapse this into
    a truthiness or ``isinstance(dict)`` test: a ``VectorParams`` is truthy and
    is NOT a ``Mapping``, and that asymmetry is the whole bug.

    A missing ``size`` returns ``None``, never ``0``. ``0`` would be recorded
    as a real dimension and compared against the configured dim, manufacturing
    a mismatch out of a config that simply did not answer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Key used for a vector that has no name. Qdrant's own shape, preserved so
#: callers can tell "unnamed" apart from "named", instead of conflating them.
UNNAMED: None = None


def read_vector_size(spec: Any) -> int | None:
    """Read ``size`` off one vector spec, which is an object OR a mapping."""
    size = getattr(spec, "size", None)
    if size is None and isinstance(spec, Mapping):
        size = spec.get("size")
    return int(size) if isinstance(size, int) else None


def read_vector_dim(params: Any) -> int | None:
    """Return the collection's single vector dim, or None if it is not single.

    Accepts the full ``config.params`` object (what the typed client hands back)
    or a raw ``vectors`` container, so a caller that has already unwrapped it
    does not have to fake a wrapper. Returns None — not a dim, not 0 — when the
    collection is multi-vector, because no single number describes it and
    guessing one is how a false mismatch is born.
    """
    vectors = (
        params if "size" in _as_keys(params) or not hasattr(params, "vectors") else params.vectors
    )
    dims = read_vector_dims(vectors)
    if UNNAMED in dims:
        return dims[UNNAMED]
    if len(dims) == 1:
        # A lone named vector is still the collection's only vector.
        return next(iter(dims.values()))
    return None


def read_vector_dims(params: Any) -> dict[str | None, int]:
    """Normalise Qdrant vector params to ``{named_vector | None: size}``.

    This is the only place in the repo that turns a live vector config into
    dims. Readers and writers that parse it separately are how a named-vector
    reader and a bare-vector reader came to disagree about the same collection.
    """
    raw = params if not hasattr(params, "vectors") else params.vectors
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        out: dict[str | None, int] = {}
        for name, spec in raw.items():
            size = read_vector_size(spec)
            if size is not None:
                out[name] = size
        return out
    size = read_vector_size(raw)
    return {UNNAMED: size} if size is not None else {}


def resolve_requested_dim(
    dims: Mapping[str | None, int],
    vector_name: str | None,
) -> int | None:
    """Resolve the dim a requested vector name means, across both shapes.

    Returns the dim, or None when the request cannot be satisfied. The bare
    case is the subtle one: a named-vector request against a collection that
    exposes a single UNNAMED vector is satisfiable — the collection is that one
    vector, and Qdrant accepts an unnamed query vector against it. Rejecting it
    would turn a healthy collection into ``UnknownCollection``, which is the
    false-mismatch bug one layer up. A genuinely multi-named collection still
    refuses, because there the name is a real selector and guessing is wrong.
    """
    if vector_name is not None and vector_name not in dims:
        if UNNAMED in dims and len(dims) == 1:
            return dims[UNNAMED]
        return None
    if vector_name is not None:
        return dims.get(vector_name)
    present = dims.get(UNNAMED)
    if present is None and len(dims) == 1:
        return next(iter(dims.values()))
    return present


def _as_keys(params: Any) -> Mapping[Any, Any]:
    return params if isinstance(params, Mapping) else {}


__all__ = [
    "UNNAMED",
    "read_vector_dim",
    "read_vector_dims",
    "read_vector_size",
    "resolve_requested_dim",
]
