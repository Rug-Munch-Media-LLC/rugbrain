"""Enforce a source's storage tier on an ``IngestDoc`` before persistence.

One job: given a document and its declared ``StorageRight``, either return a
compliant copy or raise ``RightViolation``. Nothing here fetches or writes.
"""

from __future__ import annotations

from dataclasses import replace

from rugbrain.ingest.rights import (
    EXCERPT_MAX_CHARS,
    POINTER_METADATA_ALLOW,
    StorageRight,
)


class RightViolation(ValueError):
    """A document exceeds what its source's storage tier permits."""


def enforce_storage(doc, right: StorageRight):
    """Return *doc* trimmed to *right*, or raise ``RightViolation``."""
    body = doc.content or ""

    if right is StorageRight.FULL_TEXT:
        return doc

    if right is StorageRight.EXCERPT_CITE:
        if len(body) <= EXCERPT_MAX_CHARS:
            return doc
        raise RightViolation(
            f"{doc.source}: excerpt_cite body {len(body)} > {EXCERPT_MAX_CHARS} chars"
        )

    if right is StorageRight.POINTER_ONLY:
        if body:
            raise RightViolation(f"{doc.source}: pointer_only forbids body text ({len(body)} chars)")
        extra = set(doc.metadata) - POINTER_METADATA_ALLOW
        if extra:
            raise RightViolation(f"{doc.source}: pointer_only metadata keys not allowed: {sorted(extra)}")
        return replace(doc, content="")

    raise RightViolation(f"{doc.source}: tier {right} must not reach storage")


__all__ = ["RightViolation", "enforce_storage"]
