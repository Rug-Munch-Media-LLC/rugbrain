"""Qdrant guardrail — exclude untrusted partitions from shared searches."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .trust import UNTRUSTED_PARTITIONS


def build_trusted_partition_filter(
    partitions: Iterable[str] = UNTRUSTED_PARTITIONS,
    field: str = "partition",
) -> dict[str, Any] | None:
    """Build a Qdrant ``must_not`` payload filter excluding untrusted partitions.

    Returns ``{"must_not": [{"key": field, "match": {"value": p}} ...]}``
    or ``None`` when *partitions* is empty. Combine with caller filters that
    carry a ``must`` key (Qdrant merges ``must`` + ``must_not``).

    This implements the "never query user-uploaded partitions" rule — attach
    it to every search on shared collections so poisoned user partitions are
    excluded before vector retrieval.
    """
    parts = tuple(partitions)
    if not parts:
        return None
    return {
        "must_not": [{"key": field, "match": {"value": p}} for p in parts],
    }
