"""Read the live collection's vector width out of Qdrant.

One job: turn a Qdrant collection-info payload into ``(width, how_it_was_read)``.
Pure parsing — no I/O, no embedder, no knowledge of what the width *should* be.
The comparison against the embedder and against the repo's declaration lives in
:mod:`rugbrain.eval.dim_preflight`.

READ-ONLY by contract: a preflight that mutates the store it measures is not a
measurement.
"""

from __future__ import annotations

from typing import Any

__all__ = ["extract_store_dim"]


def extract_store_dim(info: dict[str, Any]) -> tuple[int, str]:
    """Read the configured vector width out of a Qdrant collection info.

    Handles all three shapes: named vectors (a dict of name -> config), unnamed
    vectors (a bare config dict), and an absent config (0 = unknown). Returns
    ``(width, how_it_was_read)`` so a failure report can say which shape it saw
    rather than just "mismatch".
    """
    params = (info.get("config") or {}).get("params") or {}
    vectors = params.get("vectors")
    # Legacy unnamed shape: vectors={"size": N, "distance": ...}. Checked
    # FIRST because it is a dict too -- treating it as a name->config map
    # yields an empty set and would report a false split on every
    # pre-migration collection.
    if isinstance(vectors, dict) and isinstance(vectors.get("size"), int):
        return vectors["size"], "config.params.vectors.size (unnamed)"
    if isinstance(vectors, dict):
        sizes = {
            name: (cfg or {}).get("size") for name, cfg in vectors.items() if isinstance(cfg, dict)
        }
        sizes = {n: s for n, s in sizes.items() if isinstance(s, int)}
        if not sizes:
            return 0, "config.params.vectors: no integer size"
        if len(sizes) == 1:
            name, size = next(iter(sizes.items()))
            return size, f"config.params.vectors[{name}].size"
        widths = sorted(set(sizes.values()))
        if len(widths) == 1:
            return widths[0], f"config.params.vectors: all {len(sizes)} named vectors"
        return 0, f"config.params.vectors: MIXED widths {sizes}"
    if isinstance(vectors, int):
        return vectors, "config.params.vectors (legacy int)"
    return 0, "config.params.vectors: absent"
