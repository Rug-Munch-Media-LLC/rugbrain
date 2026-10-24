"""Extracted from the original module — moved verbatim."""

from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _haystack(row: dict[str, Any]) -> str:
    """Lowercased label + name + entity_type, the fields a classifier reads."""
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    parts = [
        _text(row.get("label")),
        _text(row.get("name")),
        _text(row.get("entity_type")),
        _text(meta.get("label")),
        _text(meta.get("name")),
        _text(meta.get("entity_type")),
        _text(row.get("content")),
    ]
    return " ".join(parts).lower()
