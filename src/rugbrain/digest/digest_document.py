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
a result dict always comes back. The two storage sinks and the outcome
they produce live in ``digest_sinks``; this module is the decision.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from rugbrain.digest.digest_sinks import (
    QdrantWriter,
    outcome_reason,
    prepare,
    write_to_graph,
    write_to_qdrant,
)
from rugbrain.digest.entities import EntitySet, extract
from rugbrain.digest.graph import GraphWriteResult
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

    point_id, payload = prepare(
        collection=target,
        clean=scrub(clean),
        source=source,
        entities=entities,
        external_id=external_id,
        published_at=published_at,
    )
    written, failed = await write_to_qdrant(
        writer, embedder, content=clean, target=target, point_id=point_id, payload=payload
    )
    graph_result = write_to_graph(
        graph_driver, point_id=point_id, clean=clean, name=name, source=source, entities=entities
    )

    return DigestResult(
        ok=True,
        collection=target,
        point_id=point_id,
        entities=entities,
        injection_flagged=flagged,
        qdrant_written=written,
        graph=graph_result,
        reason=outcome_reason(written, graph_result, failed),
    )
