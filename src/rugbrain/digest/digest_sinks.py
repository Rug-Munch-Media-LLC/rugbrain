"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
The two storage sinks of a digestion, and the outcome they produce.

Qdrant is the source of truth; the Neo4j episode is enrichment. Both are
optional at runtime and neither may raise into an ingest path, so each
returns a value the caller reports rather than propagating. Keeping them
here leaves ``digest_document`` as pure orchestration: decide, then
hand the decision to these two.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from rugbrain.digest.entities import EntitySet
from rugbrain.digest.graph import GraphWriteResult, write_episode
from rugbrain.digest.payload import build_payload, stable_point_id

logger = logging.getLogger(__name__)


class QdrantWriter(Protocol):
    """The write surface this module needs. Implemented by an adapter."""

    async def upsert(
        self, collection: str, point_id: str, vector: list[float], payload: dict[str, Any]
    ) -> bool: ...


async def write_to_qdrant(
    writer: QdrantWriter,
    embedder: Any,
    *,
    content: str,
    target: str,
    point_id: str,
    payload: dict[str, Any],
) -> tuple[bool, bool]:
    """Embed and upsert. Returns ``(written, failed)``.

    Never raises: a dead Qdrant is a degraded capability, so the caller
    gets a flag to report rather than an exception to propagate.
    """
    if embedder is None:
        logger.info("no embedder supplied — skipping qdrant write for %s", point_id)
        return False, False
    try:
        return await writer.upsert(target, point_id, await embedder(content), payload), False
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("qdrant write failed for %s: %s", target, str(exc)[:140])
        return False, True


def write_to_graph(
    driver: Any,
    *,
    point_id: str,
    clean: str,
    name: str | None,
    source: str,
    entities: EntitySet,
) -> GraphWriteResult | None:
    """Write the episodic event, or return None when no driver is wired."""
    if driver is None:
        return None
    return write_episode(
        driver,
        stable_id=point_id,
        name=(name or clean[:120]).strip()[:200],
        content=clean[:20_000],
        source=source,
        entities=entities,
        severity=entities.severity,
        source_description=source,
    )


def outcome_reason(
    written: bool, graph: GraphWriteResult | None, embedder_failed: bool
) -> str | None:
    """Distinguish "the graph is carrying this" from "nothing was stored".

    A caller that ingests without an embedder still wants to know the
    episode landed, or it will report success for a write that never
    happened. ``None`` means the Qdrant write is the success.
    """
    if written:
        return None
    if graph is not None and graph.ok:
        return "graph_only"
    return "qdrant_write_failed" if embedder_failed else "not_written"


def prepare(
    *,
    collection: str,
    clean: str,
    source: str,
    entities: EntitySet,
    external_id: str | None,
    published_at: str | None,
) -> tuple[str, dict[str, Any]]:
    """Return the deterministic ``(point_id, payload)`` for this document."""
    point_id = stable_point_id(source, external_id, clean)
    payload = build_payload(
        collection=collection,
        content=clean,
        source=source,
        entities=entities,
        external_id=external_id,
        published_at=published_at,
    )
    return point_id, payload
