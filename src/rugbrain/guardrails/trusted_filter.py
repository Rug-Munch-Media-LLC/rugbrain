"""RAG-poisoning guardrail — drop or rank-down untrusted docs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def trusted_filter(
    docs: Iterable[dict[str, Any]],
    *,
    mode: str = "drop",
    trusted_sources: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter *docs* for RAG poisoning protection.

    ``mode="drop"`` (default) returns only trusted chunks. ``mode="rank"``
    keeps all chunks but appends untrusted ones to the end (ranked down),
    each tagged with ``metadata.unverified`` via :func:`tag_unverified`.
    """
    from rugbrain import guardrails as _pkg

    docs_list = list(docs)
    if mode not in ("drop", "rank"):
        raise ValueError(f"trusted_filter: mode must be 'drop' or 'rank', got {mode!r}")
    trusted = [d for d in docs_list if _pkg.is_trusted(d, trusted_sources=trusted_sources)]
    untrusted = [d for d in docs_list if not _pkg.is_trusted(d, trusted_sources=trusted_sources)]
    if mode == "drop":
        return trusted
    return trusted + [_pkg.tag_unverified(d) for d in untrusted]
