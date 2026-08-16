"""Research / regulation RSS feed sources.

Five RSS-backed sources share one thin base class (``RSSFeedSource``) that
fetches the feed and normalizes items via ``rugbrain.rss_parser.parse_rss``
(the regex-based parser already used by the web crawler / realtime alerts —
no new XML parsing surface):

  * ``trailofbits_blog_rss``  — blog.trailofbits.com (research_v2)
  * ``zellic_blog_rss``       — zellic.io research blog (research_v2)
  * ``immunefi_blog_rss``     — immunefi.com blog (research_v2)
  * ``neodyme_blog_rss``      — neodyme.io (Solana-focused, research_v2)
  * ``cftc_press_releases_rss`` — cftc.gov press-release RSS
    (regulation_enforcement)

Each item becomes one ``IngestDoc`` (doc_type ``research`` for the research
blogs, ``enforcement_action`` for CFTC). Deterministic ``doc_id``
(``<name>:<slug>:<hash>``) keeps re-ingestion idempotent; any fetch/parse
failure logs a warning and returns ``[]`` (fail-open per ``base.py``).
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.rss_parser import parse_rss

log = logging.getLogger(__name__)


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "item"


def rss_item_to_doc(source: str, item: dict, doc_type: str, collection: str) -> IngestDoc:
    """Normalize one parsed RSS item into an IngestDoc."""
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    published = str(item.get("published") or "").strip()
    summary = str(item.get("summary") or "").strip()

    content_parts = [f"{title or 'Untitled'}."]
    if summary:
        content_parts.append(summary[:5000])
    if published:
        content_parts.append(f"Published: {published}")
    if url:
        content_parts.append(f"Source: {url}")

    metadata = {
        "collection": collection,
        "published": published,
        "source_url": url,
        "doc_type": doc_type,
    }
    return IngestDoc(
        content="\n".join(content_parts) + "\n",
        doc_id=f"{source}:{_slug(title)}:{_content_hash(url)}",
        metadata=metadata,
        doc_type=doc_type,
        source=source,
        title=title or "Untitled",
        url=url,
    )


class RSSFeedSource(Source):
    """Base class for RSS-backed sources (URL + collection + doc_type)."""

    feed_url: str = ""
    collection: str = ""
    rss_doc_type: str = "research"
    default_cap: int = 50

    @property
    def default_collection(self) -> str:  # type: ignore[override]
        return self.collection

    @property
    def doc_type(self) -> str:  # type: ignore[override]
        return self.rss_doc_type

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = self.default_cap if limit is None else min(limit, self.default_cap)
        try:
            r = await client.get(self.feed_url, timeout=self.timeout)
            xml = r.text if r.status_code == 200 else ""
        except Exception as e:
            log.warning("rss_fetch_error source=%s err=%s", self.name, str(e)[:160])
            xml = ""
        items = parse_rss(xml, limit=cap)
        if not items:
            log.warning("rss_fetch_empty source=%s", self.name)
            return []
        docs = [
            rss_item_to_doc(self.name, item, self.rss_doc_type, self.collection) for item in items
        ]
        log.info("rss_feed_ok source=%s items=%d", self.name, len(docs))
        return docs


class TrailOfBitsBlogSource(RSSFeedSource):
    """blog.trailofbits.com RSS — ToB research/security-engineering writeups."""

    name = "trailofbits_blog_rss"
    collection = "research_v2"
    rss_doc_type = "research"
    feed_url = "https://blog.trailofbits.com/feed/"
    description = "Trail of Bits research blog RSS (Ethereum security engineering, research_v2)"
    timeout = 30.0


class ZellicBlogSource(RSSFeedSource):
    """zellic.io blog RSS — smart-contract exploit + audit-findings writeups."""

    name = "zellic_blog_rss"
    collection = "research_v2"
    rss_doc_type = "research"
    feed_url = "https://www.zellic.io/blog/rss.xml"
    description = "Zellic research blog RSS (exploit writeups, audit findings, research_v2)"
    timeout = 30.0


class ImmunefiBlogSource(RSSFeedSource):
    """immunefi.com blog RSS — whitehat spotlights + hack analyses."""

    name = "immunefi_blog_rss"
    collection = "research_v2"
    rss_doc_type = "research"
    feed_url = "https://immunefi.com/blog/rss/"
    description = "Immunefi blog RSS (hack analyses, bug-bounty methodology, research_v2)"
    timeout = 30.0


class NeodymeBlogSource(RSSFeedSource):
    """neodyme.io RSS — Solana/security research writeups."""

    name = "neodyme_blog_rss"
    collection = "research_v2"
    rss_doc_type = "research"
    feed_url = "https://neodyme.io/rss.xml"
    description = "Neodyme blog RSS (Solana security research, research_v2)"
    timeout = 30.0


class CFTCPressReleasesSource(RSSFeedSource):
    """cftc.gov press-release RSS — official enforcement announcements."""

    name = "cftc_press_releases_rss"
    collection = "regulation_enforcement"
    rss_doc_type = "enforcement_action"
    feed_url = "https://www.cftc.gov/PressRoom/PressReleases/PressReleasesRSS"
    description = (
        "CFTC press-release RSS (crypto fraud / manipulation enforcement, regulation_enforcement)"
    )
    timeout = 30.0


__all__ = [
    "CFTCPressReleasesSource",
    "ImmunefiBlogSource",
    "NeodymeBlogSource",
    "RSSFeedSource",
    "TrailOfBitsBlogSource",
    "ZellicBlogSource",
    "rss_item_to_doc",
]
