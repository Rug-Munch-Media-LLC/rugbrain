"""Core types for programmatic RugBrain data sources.

Defines the small, dependency-free contract every source module in this
package implements:

  * ``IngestDoc``   — canonical fetched-document shape (content + doc_id +
    metadata + doc_type + source). Chunking/embedding is deliberately *not*
    part of this contract — sources only fetch and normalize; the existing
    ``rugbrain.ingest_common.store_documents`` pipeline (or a script) owns
    chunking, embedding and Qdrant upserts.
  * ``Source``      — abstract base class with a single ``fetch()`` entry
    point plus shared HTTP client handling.

Design rules (consistent with ``sources_registry`` / ``market_data``):

  * Sources are *additive* — never touch chunking, search or Qdrant.
  * Fetchers return ``[]`` on failure with a ``log.warning`` instead of
    raising, so a flaky upstream never aborts a batch.
  * Deterministic ``doc_id`` values (``<source>:<stable key>:<content hash>``)
    keep re-ingestion idempotent, matching ``CrawlDocument.stable_id``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "RMI-RagBrain/0.1 (+https://rugmunch.io; source ingestion)"
DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class IngestDoc:
    """A fetched document ready for chunking + embedding.

    Fields:

      * ``content`` — clean text payload (markdown, code, or structured text).
      * ``doc_id``  — deterministic unique ID (``source:key:contenthash``).
      * ``metadata``— arbitrary filterable payload (chain, severity, date, …).
      * ``doc_type``— semantic type (e.g. ``exploit_poc``, ``standard``).
      * ``source``  — source name (e.g. ``defihacklabs``).
      * ``title``/``url`` — optional convenience fields mirrored into
        ``metadata`` by ``to_crawl_document``.
    """

    content: str
    doc_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    doc_type: str = ""
    source: str = ""
    title: str = ""
    url: str = ""


class Source(ABC):
    """Abstract programmatic data source.

    Subclasses set the class attributes (``name``, ``doc_type``,
    ``default_collection``, ``description``) and implement
    ``_fetch(client, limit)``. ``fetch()`` manages the shared HTTP client.
    """

    name: str = ""
    doc_type: str = ""
    default_collection: str = ""
    description: str = ""
    timeout: float = DEFAULT_TIMEOUT

    @abstractmethod
    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        """Fetch + normalize documents. Never raises; returns [] on failure."""

    async def fetch(self, limit: int | None = None) -> list[IngestDoc]:
        """Fetch up to *limit* documents (None = source default)."""
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            try:
                docs = await self._fetch(client, limit)
            except Exception as e:
                log.warning("source_fetch_failed name=%s err=%s", self.name, str(e)[:200])
                return []
            return docs[:limit] if limit else docs


def to_crawl_document(doc: IngestDoc) -> Any:
    """Bridge an ``IngestDoc`` to the existing ``CrawlDocument`` shape.

    Lets the new programmatic sources feed the established
    ``ingest_common.store_documents`` embedding pipeline unchanged.
    Imported lazily to keep this module dependency-light.
    """
    from rugbrain.ingest_common import doc_from_article

    metadata = dict(doc.metadata)
    metadata.setdefault("doc_type", doc.doc_type)
    metadata.setdefault("title", doc.title)
    metadata.setdefault("url", doc.url)
    return doc_from_article(
        title=doc.title,
        content=doc.content,
        url=doc.url,
        source=doc.source,
        collection=str(metadata.get("collection", "") or ""),
        category=doc.doc_type,
        metadata=metadata,
    )


__all__ = ["DEFAULT_TIMEOUT", "USER_AGENT", "IngestDoc", "Source", "to_crawl_document"]
