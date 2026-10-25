"""Chain and analytics docs — the ``llms.txt`` surface, fetched bounded.

Base, Arbitrum and DefiLlama publish ``llms.txt``: a machine index of their
canonical pages, one link per topic. Read the index, fetch a bounded number of linked ``.md`` pages.

  * ``base_docs``      — docs.base.org (Base L2, Coinbase)      -> protocol_design
  * ``arbitrum_docs``  — docs.arbitrum.io (Arbitrum, OffchainLabs) -> protocol_design
  * ``defillama_docs`` — docs.llama.fi (DeFi TVL methodology)   -> protocol_design

Each page carries publisher, licence, source_url, doc_type and a
deterministic canonical id. Fetches fail open; a dead page is a warning.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

COLLECTION = "protocol_design"
MAX_PAGE_CHARS = 40000
_LINK_RE = re.compile(r"^\s*[-*]\s*\[([^\]]{3,200})\]\(([^)\s]+)\)", re.MULTILINE)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _slug(value: str) -> str:
    """URL-safe key fragment for deterministic doc ids."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80] or "page"


@dataclass(frozen=True)
class DocsSite:
    """Static provenance and fetch shape for one documentation site."""

    publisher: str
    index_url: str
    base_url: str
    license: str
    description: str = ""
    suffix: str = ".md"


SITES: tuple[DocsSite, ...] = (
    DocsSite(
        publisher="Base (Coinbase)",
        index_url="https://docs.base.org/llms.txt",
        base_url="https://docs.base.org/",
        license="Copyright Coinbase/Base - documentation site, no reuse licence declared",
        suffix=".md",
        description="Base L2 documentation: chain primitives, SDKs, payments, upgrades",
    ),
    DocsSite(
        publisher="Offchain Labs (Arbitrum)",
        index_url="https://docs.arbitrum.io/llms.txt",
        base_url="https://docs.arbitrum.io/",
        license="UNDECLARED - OffchainLabs/arbitrum-docs carries no SPDX licence file",
        suffix="",
        description="Arbitrum documentation: bridging, nodes, chains, Stylus",
    ),
    DocsSite(
        publisher="DefiLlama",
        index_url="https://docs.llama.fi/llms.txt",
        base_url="https://docs.llama.fi/",
        license="Copyright DefiLlama - documentation site, no reuse licence declared",
        suffix="",
        description="DefiLlama docs: TVL methodology, listing projects, adapter SDK",
    ),
)


def parse_llms_index(text: str, site: DocsSite) -> list[tuple[str, str]]:
    """``[(title, absolute_url)]`` from an ``llms.txt`` index, deduped by URL."""
    seen: dict[str, str] = {}
    for title, href in _LINK_RE.findall(text or ""):
        if href.startswith("#"):
            continue
        url = urljoin(site.base_url, href.strip())
        if site.suffix and not url.endswith(site.suffix):
            url = f"{url}{site.suffix}"
        seen.setdefault(url, title.strip())
    return [(t, u) for u, t in seen.items()]


class LlmsDocsSource(Source):
    site: DocsSite = SITES[0]
    default_cap = 12
    default_collection = COLLECTION
    timeout = 30.0

    @property
    def doc_type(self) -> str:  # type: ignore[override]
        return "protocol_doc"

    @property
    def description(self) -> str:  # type: ignore[override]
        return self.site.description

    @property
    def publisher_name(self) -> str:
        """Human publisher name, for the manifest and payload audit."""
        return self.site.publisher

    def _doc(self, title: str, url: str, body: str, published: str = "") -> IngestDoc:
        """One docs page as an IngestDoc with full provenance."""
        text = body.strip()[:MAX_PAGE_CHARS]
        header = f"# {title}\nPublisher: {self.site.publisher}\nSource: {url}\n\n"
        metadata: dict[str, Any] = {
            "collection": COLLECTION,
            "publisher": self.site.publisher,
            "license": self.site.license,
            "source_url": url,
            "published": published,
            "doc_type": "protocol_doc",
            "canonical_id": f"{self.name}:{_slug(title)}:{_content_hash(url)}",
        }
        return IngestDoc(
            content=header + text,
            doc_id=metadata["canonical_id"],
            metadata=metadata,
            doc_type="protocol_doc",
            source=self.name,
            title=title,
            url=url,
        )

    async def _page(self, client: httpx.AsyncClient, title: str, url: str) -> IngestDoc | None:
        """Fetch one page; ``None`` when it is unreachable or empty."""
        try:
            r = await client.get(url, timeout=self.timeout)
        except Exception as e:
            log.warning("docs_page_error source=%s url=%s err=%s", self.name, url, str(e)[:120])
            return None
        if r.status_code != 200 or len(r.text.strip()) < 200:
            return None
        return self._doc(title, url, r.text)

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = self.default_cap if limit is None else min(limit, self.default_cap)
        try:
            r = await client.get(self.site.index_url, timeout=self.timeout)
            index = r.text if r.status_code == 200 else ""
        except Exception as e:
            log.warning("docs_index_error source=%s err=%s", self.name, str(e)[:160])
            return []
        pages = parse_llms_index(index, self.site)[:cap]
        if not pages:
            log.warning("docs_index_empty source=%s", self.name)
            return []
        docs = [d for title, url in pages if (d := await self._page(client, title, url))]
        log.info("docs_ok source=%s pages=%d", self.name, len(docs))
        return docs


class BaseDocsSource(LlmsDocsSource):
    """docs.base.org."""

    name = "base_docs"
    site = SITES[0]


class ArbitrumDocsSource(LlmsDocsSource):
    """docs.arbitrum.io."""

    name = "arbitrum_docs"
    site = SITES[1]


class DefiLlamaDocsSource(LlmsDocsSource):
    """docs.llama.fi."""

    name = "defillama_docs"
    site = SITES[2]


__all__ = [
    "SITES",
    "ArbitrumDocsSource",
    "BaseDocsSource",
    "DefiLlamaDocsSource",
    "DocsSite",
    "LlmsDocsSource",
    "parse_llms_index",
]
