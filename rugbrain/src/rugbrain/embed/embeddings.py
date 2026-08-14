"""Infinity /embeddings client (bge-m3), cache, backend selection.

TODO(rugbrain-extract): port this module from app/rag/embeddings.py into this location.
Until ported, importing it raises NotImplementedError.
"""

from __future__ import annotations


def __getattr__(name: str) -> object:
    raise NotImplementedError(
        f"rugbrain module {__name__!r} is a scaffold stub. "
        f"Port app/rag/embeddings.py first (see MIGRATION.md step 2/3)."
    )


__all__: list[str] = []
