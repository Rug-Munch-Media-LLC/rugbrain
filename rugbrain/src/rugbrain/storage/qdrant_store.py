"""Unified hybrid dense+sparse store (named vectors, RRF k=60, uuid5 idempotent upserts, fail-open).

TODO(rugbrain-extract): port this module from app/rag/qdrant_store.py into this location.
Until ported, importing it raises NotImplementedError.
"""

from __future__ import annotations


def __getattr__(name: str) -> object:
    raise NotImplementedError(
        f"rugbrain module {__name__!r} is a scaffold stub. "
        f"Port app/rag/qdrant_store.py first (see MIGRATION.md step 2/3)."
    )


__all__: list[str] = []
