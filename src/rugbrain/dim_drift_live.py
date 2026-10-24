"""Live half of the dimension-drift gate: GET-only Qdrant census."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

from rugbrain.dim_drift_registry import (
    _READ_ONLY,
    _REQUEST_TIMEOUT,
    CANONICAL_DIM,
    default_qdrant_url,
)
from rugbrain.dim_drift_types import LiveCheck
from rugbrain.qdrant_store.collection_schema import dense_vector_dimensions

# ── Live half ──────────────────────────────────────────────────────────────


def _get_json(url: str, timeout: float = _REQUEST_TIMEOUT) -> Any:
    """GET a URL and decode JSON. The only network verb this module uses."""
    request = urllib.request.Request(url, method=_READ_ONLY)  # noqa: S310 — internal Qdrant/RugBrain URL
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — internal Qdrant/RugBrain URL
        return json.loads(response.read().decode("utf-8"))


def _shape_of(vectors: Any) -> str:
    """Name the ``params.vectors`` shape Qdrant returned.

    Qdrant 1.x returns EITHER a bare single-vector object
    (``{"size": N, "distance": ...}``) OR a named-vector dict
    (``{"dense": {"size": N, "distance": ...}}``) when sparse vectors are
    present. Both shapes occur at 4096 on this host (90 bare, 6 named as of
    2026-09-29). Naming the shape is what stops the failure this check was
    written after: a parser that handles only one shape reports healthy
    populated collections as "no vector config".
    """
    if not isinstance(vectors, dict):
        return f"not-an-object:{type(vectors).__name__}"
    if isinstance(vectors.get("size"), int):
        return "bare"
    if any(isinstance(spec, dict) and "size" in spec for spec in vectors.values()):
        return "named"
    return "empty"


def scan_live(
    qdrant_url: str | None = None,
    *,
    timeout: float = _REQUEST_TIMEOUT,
    collections: Sequence[str] | None = None,
) -> LiveCheck:
    """GET-only census of every Qdrant collection's vector width.

    Any collection whose width is not :data:`CANONICAL_DIM` is a failure.
    There is no allowlist and no "expected" exception list: the whole point
    of the elimination is that every collection ends at the canonical dim,
    because the 2560 bases get deleted. A 1024-dim collection is a bug, not
    a special case, and a check that excuses it has not enforced anything.

    *collections* exists for the test's mutation harness, which injects a
    synthetic collection without touching production.
    """
    url = (qdrant_url or default_qdrant_url()).rstrip("/")
    try:
        if collections is None:
            listing = _get_json(f"{url}/collections", timeout)
            names = sorted(c["name"] for c in listing.get("result", {}).get("collections", []))
        else:
            names = sorted(collections)
    except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError) as exc:
        return LiveCheck(
            canonical_dim=CANONICAL_DIM, reachable=False, error=f"{type(exc).__name__}: {exc}"
        )

    offenders: list[tuple[str, tuple[int, ...], str]] = []
    unparseable: list[tuple[str, str]] = []
    for name in names:
        try:
            result = _get_json(f"{url}/collections/{name}", timeout).get("result") or {}
        except (urllib.error.URLError, OSError, ValueError, KeyError, TimeoutError) as exc:
            unparseable.append((name, f"probe failed: {type(exc).__name__}: {exc}"))
            continue
        params = (result.get("config") or {}).get("params") or {}
        vectors = params.get("vectors")
        shape = _shape_of(vectors)
        # Reuse the repo's own parser rather than re-reading the schema: this
        # is what guarantees the gate and the write path agree about a width.
        dims = tuple(sorted(dense_vector_dimensions(params)))
        if not dims:
            unparseable.append(
                (
                    name,
                    f"no dense vector config (params.vectors shape={shape}, "
                    f"raw={json.dumps(vectors)[:120]})",
                )
            )
            continue
        if dims != (CANONICAL_DIM,):
            offenders.append(
                (
                    name,
                    dims,
                    f"not the canonical {CANONICAL_DIM}-dim lane "
                    f"(shape={shape}, {result.get('points_count', 0)} points)",
                )
            )

    return LiveCheck(
        canonical_dim=CANONICAL_DIM,
        reachable=True,
        checked=len(names),
        offenders=tuple(offenders),
        unparseable=tuple(unparseable),
    )
