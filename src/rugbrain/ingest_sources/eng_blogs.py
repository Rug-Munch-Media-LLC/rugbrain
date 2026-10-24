"""Fetch the declared engineering-blog feeds into the research lane.

The RSS half of the devdocs ingest; :mod:`rugbrain.ingest_sources.devdocs` is the static
half. Feeds come from
:data:`~rugbrain.ingest_sources.devdocs_specs.FEED_SITES` and are parsed with the fleet's
existing regex parser (:func:`rugbrain.rss_parser.parse_rss`) — no new XML surface, and
the same normalizer the web crawler and realtime alerts already trust.

WHY BLOGS ARE IN THE CORPUS AT ALL
    A protocol spec states what is required; it does not state why the alternative was
    rejected, what broke in production, or what the team would do differently. That
    reasoning lives in engineering writing (Flashbots on PBS ordering, Paradigm on MEV
    and AMM design, Base on L2 sequencing, a16z crypto on mechanism design). It is the
    thin part of the corpus — ``research_v2`` held 164 points against a 2.13M code corpus
    before this source existed — so the marginal document here is worth more than the
    marginal Solidity file.

ONE DOC PER ITEM, CAPPED, FAIL-OPEN
    A feed that 404s or moves is recorded as a failure and skipped; the batch continues.
    Counts are returned so a run that quietly lost a publisher is visible rather than
    looking like a publisher that simply had nothing to say.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.devdocs_page import content_hash, slug
from rugbrain.ingest_sources.devdocs_specs import BLOG_COLLECTION, FEED_SITES, DocSite
from rugbrain.rss_parser import parse_rss

log = logging.getLogger(__name__)

FEED_TIMEOUT = 25.0


async def collect_feeds(
    client: httpx.AsyncClient,
    limit: int | None = None,
    sites: tuple[DocSite, ...] = FEED_SITES,
) -> tuple[list[IngestDoc], dict[str, Any]]:
    """Fetch every declared feed; return ``(docs, stats)``.

    Each item becomes one document carrying its publication date, its canonical family
    URL and the work's licence, so a surviving point is self-describing.
    """
    docs: list[IngestDoc] = []
    seen: set[str] = set()
    stats: dict[str, Any] = {
        "requested": 0,
        "emitted": 0,
        "items_seen": 0,
        "duplicates": 0,
        "failed": 0,
        "by_source": {},
        "failures": [],
    }
    for site in sites:
        stats["requested"] += 1
        per: dict[str, int] = {"items": 0, "emitted": 0, "failed": 0}
        feed_url = site.urls[0] if site.urls else ""
        try:
            response = await client.get(feed_url, timeout=FEED_TIMEOUT)
        except Exception as exc:
            stats["failed"] += 1
            per["failed"] += 1
            stats["failures"].append({"url": feed_url, "error": str(exc)[:160]})
            log.warning("eng_blog_fetch_failed url=%s err=%s", feed_url, str(exc)[:160])
            stats["by_source"][site.name] = per
            continue
        if response.status_code != 200:
            stats["failed"] += 1
            per["failed"] += 1
            stats["failures"].append({"url": feed_url, "status": response.status_code})
            log.warning("eng_blog_non200 url=%s status=%d", feed_url, response.status_code)
            stats["by_source"][site.name] = per
            continue
        items = parse_rss(response.text or "")
        stats["items_seen"] += len(items)
        per["items"] = len(items)
        for item in items[: site.cap]:
            if limit is not None and len(docs) >= limit:
                break
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            published = str(item.get("published") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if not title and not summary:
                continue
            body = "\n".join(
                part
                for part in (
                    f"{title}." if title else "",
                    summary[:6000],
                    f"Published: {published}" if published else "",
                    f"Publisher: {site.family}",
                    f"Licence: {site.licence}",
                    f"Canonical source: {site.url}",
                    f"Source: {url}" if url else "",
                )
                if part
            )
            fingerprint = content_hash(body[:4000])
            if fingerprint in seen:
                stats["duplicates"] += 1
                continue
            seen.add(fingerprint)
            docs.append(
                IngestDoc(
                    content=body + "\n",
                    doc_id=f"{site.name}:{slug(url or title)}:{fingerprint}",
                    doc_type=site.doc_type,
                    source=site.name,
                    title=title or "Untitled",
                    url=url,
                    metadata={
                        "collection": site.collection,
                        "family": site.family,
                        "licence": site.licence,
                        "canonical_url": site.url,
                        "source_url": url,
                        "published": published,
                        "doc_type": site.doc_type,
                        "ingest_group": "devdocs",
                    },
                )
            )
            stats["emitted"] += 1
            per["emitted"] += 1
        stats["by_source"][site.name] = per
    return docs, stats


class EngineeringBlogsSource(Source):
    """Registered source over :data:`FEED_SITES` (Solana, Base, Flashbots, Paradigm, a16z)."""

    name = "eng_blogs"
    doc_type = "engineering_blog"
    default_collection = BLOG_COLLECTION
    description = "Engineering blogs: Solana, Base, Flashbots, Paradigm, a16z crypto (RSS)"

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        docs, _stats = collect_feeds(client, limit=limit)
        return docs


__all__ = ["FEED_TIMEOUT", "EngineeringBlogsSource", "collect_feeds"]
