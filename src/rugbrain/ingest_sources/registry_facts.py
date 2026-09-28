"""The two facts a registry audit cannot own: the owned set and Qdrant.

Both are read from somewhere else and are therefore reported as *data* rather
than asserted by this module. An unreachable Qdrant yields an empty live set
and the caller treats existence as "unknown", not as a failure.
"""

from __future__ import annotations

import json
from typing import Any


def owned_collections() -> set[str]:
    """Read the canonical owned-set. Imported lazily: this is a registry module."""
    from rugbrain.qdrant_store.collections import _RMI_RAG_OWNED_COLLECTIONS

    return set(_RMI_RAG_OWNED_COLLECTIONS)


def live_collections(qdrant_url: str = "http://localhost:6334") -> set[str]:
    """GET ``/collections`` from Qdrant. Read-only; empty set when unreachable.

    Never writes: this is a GET, and the registry must stay safe to run from
    any cron. An unreachable Qdrant is reported as an empty live set and the
    caller treats existence as "unknown", not as a failure.
    """
    import urllib.request

    if not qdrant_url.startswith("http://") and not qdrant_url.startswith("https://"):
        return set()
    try:
        with urllib.request.urlopen(  # noqa: S310 — scheme validated above
            f"{qdrant_url}/collections", timeout=10
        ) as resp:
            payload = json.loads(resp.read())
    except Exception:
        return set()
    return {str(c.get("name", "")) for c in payload.get("result", {}).get("collections", [])}


def source_modules() -> dict[str, Any]:
    """``{module_name: module_object}`` for every module holding a Source."""
    import sys

    from rugbrain.ingest_sources import SOURCES

    out: dict[str, Any] = {}
    for src in SOURCES:
        mod = type(src).__module__
        out[mod.rsplit(".", 1)[-1]] = sys.modules[mod]
    return out


__all__ = ["live_collections", "owned_collections", "source_modules"]
