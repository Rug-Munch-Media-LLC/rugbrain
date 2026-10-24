"""Extracted from the original module — moved verbatim."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

_NAMED_CACHE: dict[str, bool] = {}
_WIDTH_CACHE: dict[str, int | None] = {}


class LaneMismatch(RuntimeError):
    """The resolved collection's live width matches no embedding lane we hold."""


def _named_vectors(base: str, collection: str) -> bool:
    """True when the collection stores named vectors (dense + sparse)."""
    if collection in _NAMED_CACHE:
        return _NAMED_CACHE[collection]
    try:
        with urllib.request.urlopen(f"{base}/collections/{collection}", timeout=30) as resp:  # noqa: S310
            vectors = json.load(resp)["result"]["config"]["params"].get("vectors") or {}
        named = isinstance(vectors, dict) and "size" not in vectors
    except Exception:
        named = False
    _NAMED_CACHE[collection] = named
    return named


def _live_width(base: str, collection: str) -> int | None:
    """Live vector width of *collection*, or None when it cannot be read.

    Read live rather than declared because the declared table and the physical
    collection have already drifted from each other once on this exact axis —
    ``lane_widths.py`` exists solely to record that lesson. A GET on
    ``/collections/{name}`` is cheap and cannot lie about what is stored.
    """
    if collection in _WIDTH_CACHE:
        return _WIDTH_CACHE[collection]
    try:
        with urllib.request.urlopen(f"{base}/collections/{collection}", timeout=30) as resp:  # noqa: S310
            vectors = json.load(resp)["result"]["config"]["params"].get("vectors") or {}
        if "size" in vectors:
            width: int | None = int(vectors["size"])
        else:  # named vectors — the dense one is the lane that matters
            named = vectors.get("dense") or (next(iter(vectors.values())) if vectors else None)
            width = int(named["size"]) if isinstance(named, dict) else None
    except Exception:
        width = None
    _WIDTH_CACHE[collection] = width
    return width


async def _embed_for_width(width: int, query: str) -> list[float]:
    """Embed *query* on the lane whose width is *width*.

    The two lanes are both real and both in service; the canonical one is
    4096 (Qwen3-Embedding-8B) and the preserved one is 2560 (Qwen3-Embedding-
    4B, local :8004) for the collections that never migrated. Kept in one place
    so the mapping "width -> embedder" is stated once and cannot drift the way
    the name->collection mapping did.
    """
    if width == 4096:
        from rugbrain.embeddings import get_embedding

        return await get_embedding(query, query=True)
    if width == 2560:
        from rugbrain.query_embedder import embed_query

        return await embed_query(query)
    raise LaneMismatch(f"no embedding lane for width {width}; refusing to guess")
