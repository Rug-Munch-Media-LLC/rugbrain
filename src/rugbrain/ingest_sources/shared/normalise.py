"""The normalise contract every source hands downstream.

One job — state the shape the store accepts. It is a ``Protocol``, not a base
class: ``base.IngestDoc`` already satisfies it structurally, and a source may
carry its own richer record (``papers_norm.SourceDoc``) until its adapter
lands. ``stable_doc_id`` is the single derivation rule for re-ingestion.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Normalised(Protocol):
    """Fetched, cleaned document ready for chunk → embed → store."""

    content: str
    doc_id: str
    metadata: dict[str, Any]


def stable_doc_id(source: str, key: str, content: str) -> str:
    """Deterministic id: same upstream document → same id → upsert."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return f"{source}:{key}:{digest}"


__all__ = ["Normalised", "stable_doc_id"]
