"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
The end-to-end digestion call, and the result it reports.

**Untrusted in.** ``content`` is sanitized on the way in (invisible
characters stripped) and on the way out (secrets redacted). The extractor
is regex-only and never calls a model, so there is no instruction channel
in the default path at all.

Order is deliberate: extract first (cheap, offline), then flag, then
decide. An empty or whitespace document never reaches Qdrant or Neo4j —
the cheapest possible rejection is the right one. Nothing here raises:
a result dict always comes back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from rugbrain.digest.entities import EntitySet, extract
from rugbrain.digest.graph import GraphWriteResult, write_episode
from rugbrain.digest.payload import build_payload, stable_point_id
from rugbrain.digest.routing import (
    COLLECTION_NEWS,
    WRITABLE_COLLECTIONS,
    route_collection,
)
from rugbrain.digest.security import has_injection, scrub, strip_invisible

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DigestResult:
    """What one digestion call did. Never raises."""

    ok: bool
    collection: str
    point_id: str | None = None
    entities: EntitySet | None = None
    injection_flagged: bool = False
    qdrant_written: bool = False
    graph: GraphWriteResult | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "collection": self.collection,
            "point_id": self.point_id,
            "entities": self.entities.as_dict() if self.entities else {},
            "injection_flagged": self.injection_flagged,
            "qdrant_written": self.qdrant_written,
            "graph": self.graph.as_dict() if self.graph else None,
            "reason": self.reason,
        }


class QdrantWriter(Protocol):
    """The write surface this module needs. Implemented by an adapter."""

    async def upsert(
        self, collection: str, point_id: str, vector: list[float], payload: dict[str, Any]
    ) -> bool: ...


async def _upsert(
    writer: QdrantWriter, embedder: Any, content: str, target: str, point_id: str, payload: dict
) -> tuple[bool, bool]:
    """Embed and write. Returns ``(written, failed)``.

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


async def digest_document(
    content: str,
    *,
    source: str,
    writer: QdrantWriter,
    embedder: Any = None,
    graph_driver: Any = None,
    external_id: str | None = None,
    published_at: str | None = None,
    collection: str | None = None,
    name: str | None = None,
) -> DigestResult:
    """Digest one document end to end."""
    if not content or not content.strip():
        return DigestResult(ok=False, collection=COLLECTION_NEWS, reason="empty_content")

    # Sanitize before anything looks at it. The flag is computed on the
    # ORIGINAL text because that is what an investigator needs to know
    # about, and it is recorded on the result for triage.
    flagged = has_injection(content)
    clean = strip_invisible(content)

    entities = extract(clean)
    target = collection or route_collection(clean, entities)
    if target not in WRITABLE_COLLECTIONS:
        return DigestResult(
            ok=False,
            collection=target,
            entities=entities,
            injection_flagged=flagged,
            reason="collection_not_writable",
        )

    point_id = stable_point_id(source, external_id, clean)
    payload = build_payload(
        collection=target,
        content=scrub(clean),
        source=source,
        entities=entities,
        external_id=external_id,
        published_at=published_at,
    )

    written, embedder_failed = await _upsert(writer, embedder, clean, target, point_id, payload)

    graph_result: GraphWriteResult | None = None
    if graph_driver is not None:
        graph_result = write_episode(
            graph_driver,
            stable_id=point_id,
            name=(name or clean[:120]).strip()[:200],
            content=clean[:20_000],
            source=source,
            entities=entities,
            severity=entities.severity,
            source_description=source,
        )

    return DigestResult(
        ok=True,
        collection=target,
        point_id=point_id,
        entities=entities,
        injection_flagged=flagged,
        qdrant_written=written,
        graph=graph_result,
        reason=_reason(written, graph_result, embedder_failed),
    )


def _reason(written: bool, graph: GraphWriteResult | None, embedder_failed: bool) -> str | None:
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
