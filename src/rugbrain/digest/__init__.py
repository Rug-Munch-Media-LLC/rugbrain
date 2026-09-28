"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Digestion — read a document or news item, extract, write, link.

The entry point for the "read and digest documents and news" mandate. One
job: take an untrusted blob of text plus its provenance, and produce

  1. a Qdrant point in the right collection with the right payload shape,
  2. a knowledge-graph episode linked to every entity found.

**Collection routing.** A document goes to exactly one collection, chosen
by its dominant signal. Routing on severity alone would send a phishing
domain list and a protocol exploit to the same place and pollute both
retrieval paths. The order below is most-specific first, and the comment
on each line is the reason it outranks the one below it.

**Qdrant payload contract.** Every point carries ``collection``,
``content``, ``source`` and ``severity`` at the top level. That is not
incidental — it is the shape the existing search, rerank and filter paths
already read, verified against live points in ``news_articles`` and
``known_scams``. A point that omits them is invisible to those filters, so
writing them is a correctness requirement, not metadata hygiene.

**Untrusted in.** ``content`` is sanitized on the way in (invisible
characters stripped) and on the way out (secrets redacted). The extractor
is regex-only and never calls a model, so there is no instruction channel
in the default path at all.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Final, Protocol

from rugbrain.digest.entities import EntitySet, extract
from rugbrain.digest.graph import GraphWriteResult, write_episode
from rugbrain.digest.security import has_injection, scrub, strip_invisible

logger = logging.getLogger(__name__)

COLLECTION_NEWS: Final = "news_articles"
COLLECTION_SCAMS: Final = "known_scams"
COLLECTION_THREAT: Final = "threat_intel_v2"
COLLECTION_SCAM_INTEL: Final = "scam_intel_v2"

# Only these may be written. A caller cannot route a document into an
# arbitrary Qdrant collection by passing a name — the allowlist is the
# boundary between "ingest content" and "write to any collection in this
# Qdrant instance".
WRITABLE_COLLECTIONS: Final = frozenset(
    {COLLECTION_NEWS, COLLECTION_SCAMS, COLLECTION_THREAT, COLLECTION_SCAM_INTEL}
)

# Ordered, most specific first.
_SCAM_ARTIFACT: Final = frozenset({"domain", "address"})
_HACK_TERMS: Final = ("exploit", "hack", "breach", "drained", "stolen funds", "vulnerability")
_PHISH_TERMS: Final = ("phishing", "drainer", "honeypot", "scam site", "impersonat")
_NEWS_TERMS: Final = ("announce", "partnership", "funding", "launch", "proposal", "upgrade")


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


def route_collection(text: str, entities: EntitySet) -> str:
    """Pick exactly one Qdrant collection for this document.

    Explicit and readable over a scored model: the decision is auditable
    in one glance, which matters when someone asks "why did this land in
    scam_intel_v2".
    """
    lowered = text.lower()
    has_hack = any(t in lowered for t in _HACK_TERMS)
    has_phish = any(t in lowered for t in _PHISH_TERMS)

    # A phishing DOMAIN is the most concrete artifact there is — a URL an
    # analyst can act on right now — so it outranks every other signal.
    if entities.domains and has_phish:
        return COLLECTION_SCAMS
    # A named protocol exploit is analysis, not an action-list entry. It
    # must be checked BEFORE the bare-address rule, or every exploit
    # writeup that quotes a stolen-funds address lands on the blocklist
    # collection and pollutes it with non-blocklist addresses.
    if has_hack:
        return COLLECTION_THREAT
    # A critical-severity document naming an address, with no exploit
    # framing, is a scam report: that address is the deliverable.
    if entities.severity == "critical" and entities.addresses:
        return COLLECTION_SCAM_INTEL
    if has_phish:
        return COLLECTION_SCAMS
    if entities.severity in ("critical", "high"):
        return COLLECTION_THREAT
    if any(t in lowered for t in _NEWS_TERMS):
        return COLLECTION_NEWS
    return COLLECTION_NEWS


def stable_point_id(source: str, external_id: str | None, content: str) -> str:
    """Deterministic Qdrant point id.

    Must be a UUID — Qdrant rejects arbitrary string ids on these
    collections. Content-hashed so re-ingesting the same document
    overwrites rather than duplicating, which matters because a news
    feed will hand us the same headline repeatedly.
    """
    basis = f"{source}|{external_id or ''}|{content[:4000]}"
    return str(uuid.UUID(hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]))


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
        "chain": chain or _infer_chain(entities),
        "metadata": metadata,
    }


def _infer_chain(entities: EntitySet) -> str:
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
    """Digest one document end to end.

    Order is deliberate: extract first (cheap, offline), then flag, then
    decide. An empty or whitespace document never reaches Qdrant or
    Neo4j — the cheapest possible rejection is the right one.
    """
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

    written = False
    embedder_failed = False
    if embedder is not None:
        try:
            vector = await embedder(clean)
            written = await writer.upsert(target, point_id, vector, payload)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("qdrant write failed for %s: %s", target, str(exc)[:140])
            embedder_failed = True
    else:
        logger.info("no embedder supplied — skipping qdrant write for %s", point_id)

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

    # Distinguish "the graph is carrying this" from "nothing was stored".
    # A caller that ingests without an embedder still wants to know the
    # episode landed, or it will report success for a write that never
    # happened.
    if written:
        reason = None
    elif graph_result is not None and graph_result.ok:
        reason = "graph_only"
    elif embedder_failed:
        reason = "qdrant_write_failed"
    else:
        reason = "not_written"

    return DigestResult(
        ok=True,
        collection=target,
        point_id=point_id,
        entities=entities,
        injection_flagged=flagged,
        qdrant_written=written,
        graph=graph_result,
        reason=reason,
    )
