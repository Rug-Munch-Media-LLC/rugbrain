"""Big crypto player research — RSS-backed institutional and lab publishing.

Three publishers that shape how the market reasons about crypto, each with a
public feed that is safe to poll:

  * ``a16zcrypto``        — a16z crypto essays/regulatory analysis (research_v2)
  * ``flashbots_writings``— MEV supply-chain research from writings.flashbots.net
                            (research_v2)
  * ``chainalysis_insights`` — Chainalysis blog: on-chain crime typologies
                            (research_v2)

Every document carries the provenance the licensing registry needs:
publisher, licence, source_url, published date, doc_type and a deterministic
canonical id (``<source>:<slug>:<contenthash>``) so re-runs overwrite rather
than duplicate. Feeds are summaries, not full articles — the stored content is
the feed's own text verbatim, which is what ``research_only`` permits.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.rss_parser import parse_rss

log = logging.getLogger(__name__)

COLLECTION = "research_v2"


def _content_hash(text: str) -> str:
    """Stable 16-hex fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _slug(value: str) -> str:
    """URL-safe key fragment for deterministic doc ids."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80] or "item"


@dataclass(frozen=True)
class Publisher:
    """Static provenance for one publisher's feed (``name`` lives on the class)."""

    publisher: str
    feed_url: str
    license: str
    doc_type: str = "research_report"
    description: str = ""


PUBLISHERS: tuple[Publisher, ...] = (
    Publisher(
        publisher="a16z crypto",
        feed_url="https://a16zcrypto.com/feed/",
        license="Copyright a16z crypto - site content, no reuse licence declared",
        description="a16z crypto essays: market structure, regulation, crypto-native design",
    ),
    Publisher(
        publisher="Flashbots",
        feed_url="https://writings.flashbots.net/rss.xml",
        license="Copyright Flashbots - site content, no reuse licence declared",
        doc_type="research_report",
        description="Flashbots writings: MEV supply chain, PBS, builder/relay research",
    ),
    Publisher(
        publisher="Chainalysis",
        feed_url="https://www.chainalysis.com/feed/",
        license="Copyright Chainalysis - site content, no reuse licence declared",
        doc_type="crime_report",
        description="Chainalysis blog: on-chain crime typologies, sanctions, exchange flows",
    ),
)


class PlayerResearchSource(Source):
    """RSS source for one institutional/lab publisher (see ``PUBLISHERS``)."""

    publisher: Publisher = PUBLISHERS[0]
    default_cap = 25
    default_collection = COLLECTION
    timeout = 30.0

    @property
    def doc_type(self) -> str:  # type: ignore[override]
        return self.publisher.doc_type

    @property
    def description(self) -> str:  # type: ignore[override]
        return self.publisher.description

    @property
    def publisher_name(self) -> str:
        """Human publisher name, for the manifest and payload audit."""
        return self.publisher.publisher

    def _doc(self, item: dict[str, Any]) -> IngestDoc:
        """Normalize one parsed feed item, stamping full provenance."""
        title = str(item.get("title") or "").strip() or "Untitled"
        url = str(item.get("url") or "").strip()
        published = str(item.get("published") or "").strip()
        summary = str(item.get("summary") or "").strip()

        parts = [f"{title}.", f"Publisher: {self.publisher.publisher}."]
        if summary:
            parts.append(summary)
        if published:
            parts.append(f"Published: {published}")
        if url:
            parts.append(f"Source: {url}")

        metadata = {
            "collection": COLLECTION,
            "publisher": self.publisher.publisher,
            "license": self.publisher.license,
            "source_url": url,
            "published": published,
            "doc_type": self.publisher.doc_type,
            "canonical_id": f"{self.name}:{_slug(title)}:{_content_hash(url)}",
        }
        return IngestDoc(
            content="\n".join(parts) + "\n",
            doc_id=metadata["canonical_id"],
            metadata=metadata,
            doc_type=self.publisher.doc_type,
            source=self.name,
            title=title,
            url=url,
        )

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = self.default_cap if limit is None else min(limit, self.default_cap)
        try:
            r = await client.get(self.publisher.feed_url, timeout=self.timeout)
            xml = r.text if r.status_code == 200 else ""
        except Exception as e:  # fail-open per base.Source
            log.warning("player_feed_error source=%s err=%s", self.name, str(e)[:160])
            xml = ""
        items = parse_rss(xml, limit=cap)
        if not items:
            log.warning("player_feed_empty source=%s status=%s", self.name, "no-items")
            return []
        docs = [self._doc(item) for item in items]
        log.info("player_feed_ok source=%s items=%d", self.name, len(docs))
        return docs


class A16ZCryptoSource(PlayerResearchSource):
    """a16z crypto feed."""

    name = "a16zcrypto"
    publisher = PUBLISHERS[0]


class FlashbotsWritingsSource(PlayerResearchSource):
    """Flashbots writings feed."""

    name = "flashbots_writings"
    publisher = PUBLISHERS[1]


class ChainalysisInsightsSource(PlayerResearchSource):
    """Chainalysis blog feed."""

    name = "chainalysis_insights"
    publisher = PUBLISHERS[2]


__all__ = [
    "PUBLISHERS",
    "A16ZCryptoSource",
    "ChainalysisInsightsSource",
    "FlashbotsWritingsSource",
    "PlayerResearchSource",
    "Publisher",
]
