"""On-chain analytics / wallet-visualisation documentation corpus.

Publishers, discovery endpoints and licences live in ``analytics_docs_targets``;
the pure parsers live in ``analytics_docs_pages``; arXiv whitepapers live in
``analytics_docs_arxiv``. This module is the fetch loop and the per-page
normalization into ``IngestDoc``.

Why one document per SECTION: the shared pipeline chunks on a fixed
2,000-character window, which cuts API parameter tables and code examples in
half. Emitting a document per heading keeps the window operating inside a
section (see ``analytics_docs_pages.split_sections``).

Ordering/robots contract:

  * ``robots.txt`` is fetched once per host and every URL is checked against
    our declared user agent. A disallowed URL is SKIPPED and counted, never
    fetched — no auth bypass, no UA spoofing, no Cloudflare circumvention.
  * Publishers whose ``status`` is ``blocked`` in the target table are not
    probed at all; their observation stays in the manifest.

Translation to the fleet pipeline: ``Source.fetch()`` returns ``IngestDoc``s,
``scripts/ingest_all_sources.py`` chunks + embeds them at 4096-dim
(Qwen3-Embedding-8B) into ``analytics_docs``.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from rugbrain.ingest_sources.analytics_docs_arxiv import arxiv_docs
from rugbrain.ingest_sources.analytics_docs_normalize import (
    DOC_TYPE,
    fetch_page,
    page_docs,
)
from rugbrain.ingest_sources.analytics_docs_pages import (
    is_page_url,
    parse_llms_txt,
    parse_sitemap,
)
from rugbrain.ingest_sources.analytics_docs_targets import PUBLISHERS, Publisher
from rugbrain.ingest_sources.base import USER_AGENT, IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "analytics_docs"
MAX_PAGES_PER_PUBLISHER = 60
#: Deterministic ceiling for one full refresh (publishers x this).
MAX_SECTIONS_PER_PUBLISHER = 60


async def _robots(client: httpx.AsyncClient, base: str) -> RobotFileParser:
    """Parse one host's robots.txt; an unreachable file means allow-all."""
    parsed = urlparse(base)
    rp = RobotFileParser()
    rp.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
    try:
        resp = await client.get(rp.url, headers={"User-Agent": USER_AGENT})
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        else:
            rp.parse([])
    except Exception as e:
        log.warning("robots_unavailable host=%s err=%s", parsed.netloc, str(e)[:120])
        rp.parse([])
    return rp


async def _discover(client: httpx.AsyncClient, pub: Publisher, cap: int) -> list[str]:
    """Page URLs for one publisher: llms.txt, then sitemap, then seeds."""
    found: list[str] = []
    if pub.llms:
        try:
            resp = await client.get(pub.llms, headers={"User-Agent": USER_AGENT})
            if resp.status_code == 200:
                found = parse_llms_txt(resp.text, pub.base)
        except Exception as e:
            log.warning("llms_failed publisher=%s err=%s", pub.name, str(e)[:120])
    if not found and pub.sitemap:
        try:
            resp = await client.get(pub.sitemap, headers={"User-Agent": USER_AGENT})
            if resp.status_code == 200:
                pages, nested = parse_sitemap(resp.text)
                for sub in nested[:5]:
                    sub_resp = await client.get(sub, headers={"User-Agent": USER_AGENT})
                    if sub_resp.status_code == 200:
                        more, _ = parse_sitemap(sub_resp.text)
                        pages.extend(more)
                found = pages
        except Exception as e:
            log.warning("sitemap_failed publisher=%s err=%s", pub.name, str(e)[:120])
    if not found:
        found = list(pub.seeds)
    pages = [u for u in found if is_page_url(u, pub.include)]
    # Dedupe preserving order; a sitemap repeats locale variants.
    return list(dict.fromkeys(pages))[:cap]


class AnalyticsDocsSource(Source):
    """Documentation + API reference of on-chain analytics and wallet apps."""

    name = "analytics_docs"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = (
        "On-chain analytics / wallet-visualisation docs: Arkham, Nansen, MetaSleuth, "
        "Breadcrumbs, GraphSense, Rotki, Blockscout, DeBank, Zerion, Cielo, SolanaFM, "
        "Step, Etherscan, Solscan, Bubblemaps + clustering/entity-resolution papers"
    )
    timeout = 90.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        from datetime import UTC, datetime

        retrieved = datetime.now(UTC).date().isoformat()
        live = [p for p in PUBLISHERS if p.status == "live"]
        if not live:
            return []
        # The budget is split PER PUBLISHER, never spent first-come-first-served.
        # An early `len(docs) >= limit` return starved every publisher after the
        # first two (measured: limit=120 produced etherscan+blockscout only), and
        # a corpus that quietly covers two of sixteen declared publishers is
        # worse than one with a smaller, even cap.
        budget = limit or MAX_SECTIONS_PER_PUBLISHER * len(live)
        per_pub_sections = max(12, budget // len(live))
        docs: list[IngestDoc] = []
        for pub in live:
            urls = await _discover(client, pub, MAX_PAGES_PER_PUBLISHER)
            rp = await _robots(client, pub.base)
            fetched = 0
            sections = 0
            for url in urls:
                if sections >= per_pub_sections:
                    break
                if not rp.can_fetch(USER_AGENT, url):
                    log.info("robots_disallow publisher=%s url=%s", pub.name, url)
                    continue
                text = await fetch_page(client, url)
                page = page_docs(pub, url, text, retrieved)
                if page:
                    fetched += 1
                    docs.extend(page)
                    sections += len(page)
            log.info(
                "publisher_done publisher=%s pages=%d sections=%d", pub.name, fetched, len(docs)
            )
        docs.extend(await arxiv_docs(client, retrieved))
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "AnalyticsDocsSource",
]
