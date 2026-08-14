"""RAG regression eval gates (RAG_EVAL_*).

TODO(rugbrain-extract): port this module from app/rag/evaluation.py into this location.
Until ported, importing it raises NotImplementedError.
"""

from __future__ import annotations


def __getattr__(name: str) -> object:
    raise NotImplementedError(
        f"rugbrain module {__name__!r} is a scaffold stub. "
        f"Port app/rag/evaluation.py first (see MIGRATION.md step 2/3)."
    )


__all__: list[str] = []
