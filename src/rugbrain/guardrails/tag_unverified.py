"""RAG-poisoning guardrail — tag an untrusted doc as unverified."""

from __future__ import annotations

from typing import Any

from .trust import _doc_metadata


def tag_unverified(doc: dict[str, Any], reason: str = "no trusted source") -> dict[str, Any]:
    """Return a copy of *doc* tagged as unverified (additive, never mutates).

    Adds ``metadata.unverified`` / ``metadata.trust_level`` /
    ``metadata.unverified_reason`` so downstream ``trusted_filter`` calls
    rank or drop it. If the doc is already trusted this is a no-op copy.
    """
    from rugbrain import guardrails as _pkg

    out = dict(doc)
    meta = dict(_doc_metadata(doc))
    if _pkg.is_trusted(out):
        return out
    meta["unverified"] = True
    meta["trust_level"] = "unverified"
    meta["unverified_reason"] = reason
    out["metadata"] = meta
    return out
