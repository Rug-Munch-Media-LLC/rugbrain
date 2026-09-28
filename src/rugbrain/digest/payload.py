"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Qdrant point identity and the payload shape the search paths read.

**The payload contract.** Every point carries ``collection``,
``content``, ``source`` and ``severity`` at the top level. That is not
incidental — it is the shape the existing search, rerank and filter paths
already read, verified against live points in ``news_articles`` and
``known_scams``. A point that omits them is invisible to those filters,
so writing them is a correctness requirement, not metadata hygiene.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from rugbrain.digest.entities import EntitySet


def stable_point_id(source: str, external_id: str | None, content: str) -> str:
    """Deterministic Qdrant point id.

    Must be a UUID — Qdrant rejects arbitrary string ids on these
    collections. Content-hashed so re-ingesting the same document
    overwrites rather than duplicating, which matters because a news
    feed will hand us the same headline repeatedly.
    """
    basis = f"{source}|{external_id or ''}|{content[:4000]}"
    return str(uuid.UUID(hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]))


def infer_chain(entities: EntitySet) -> str:
    """Best-guess chain for the payload's chain field.

    Only EVM-addressed content is claimed as ethereum. Guessing Solana
    from a protocol name would mislabel a Base incident.
    """
    if entities.evm_addresses:
        return "ethereum"
    if entities.solana_addresses:
        return "solana"
    if entities.tron_addresses:
        return "tron"
    return "unknown"


def build_payload(
    *,
    collection: str,
    content: str,
    source: str,
    entities: EntitySet,
    external_id: str | None = None,
    published_at: str | None = None,
    chain: str | None = None,
) -> dict[str, Any]:
    """Build the Qdrant payload in the shape the existing filters read.

    Top-level ``collection`` / ``content`` / ``source`` / ``severity`` are
    the contract. Entities go under ``metadata`` alongside the source's
    own metadata, mirroring the live payloads.
    """
    metadata: dict[str, Any] = dict(entities.as_dict())
    if external_id:
        metadata["external_id"] = external_id
    if published_at:
        metadata["date"] = published_at
        metadata["published_at"] = published_at
    return {
        "collection": collection,
        "content": content,
        "source": source,
        "severity": entities.severity,
        "chain": chain or infer_chain(entities),
        "metadata": metadata,
    }
