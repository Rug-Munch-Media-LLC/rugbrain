"""Qwen3-Embedding query template resolution.

The template must match the migration/search path so query-side vectors land
in the same semantic space as stored docs. The import is guarded exactly as
the former module did.
"""

from __future__ import annotations

try:
    from rugbrain.embeddings import QUERY_INSTRUCT as _QUERY_INSTRUCT
except Exception:  # pragma: no cover - fallback if import order breaks
    _QUERY_INSTRUCT = (
        "Instruct: Given a web search query, retrieve relevant passages that "
        "answer the query\nQuery: {}"
    )

__all__ = ["_QUERY_INSTRUCT"]
