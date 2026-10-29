"""Public Telegram-bot, launchpad and whitepaper docs source.

Every document comes from the manifest (``docs_manifest``) and is fetched from
a PUBLIC page — docs sites, help pages, ``llms-full.txt`` bundles, raw READMEs
and hosted PDFs. No private groups, no authenticated surfaces, no bypass.

Extraction goes through ``document_to_ingest_doc`` so the corpus records WHICH
parser produced the text (``parser``/``parser_version``/``parser_reason``):
PDFs prefer Docling when ``DOCLING_URL``+``DOCLING_ENABLED`` are set, HTML goes
to markitdown, and an unreadable page keeps an index stub instead of vanishing.

Doc ids are ``docs_manifest.canonical_id`` plus a content hash, so a re-run
over unchanged pages is idempotent and a changed page is a new point.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.docling_config import DoclingSettings, settings_from_env
from rugbrain.ingest_sources.docling_ingest import DocumentRequest, document_to_ingest_doc
from rugbrain.ingest_sources.docs_manifest import ManifestEntry, canonical_id, load_manifest

log = logging.getLogger(__name__)

SOURCE_NAME = "bots_launchpads"
DEFAULT_COLLECTION = "research_v2_8b"
MIN_WORDS = 120


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _doc_type_for(entry: ManifestEntry, url: str) -> str:
    return entry.doc_type


def _is_pdf(url: str, content_type: str) -> bool:
    return url.lower().endswith(".pdf") or "application/pdf" in content_type


def _settings_for_pdf() -> DoclingSettings | None:
    """Docling only for PDFs: HTML is faster and exact via markitdown."""
    settings = settings_from_env()
    return settings if settings.usable else None


def _ingest_fields(entry: ManifestEntry, url: str) -> dict[str, str]:
    """Provenance every document carries, from the manifest row."""
    return {
        "publisher": entry.publisher,
        "category": entry.category,
        "doc_type": entry.doc_type,
        "license": entry.license,
        "retrieved_on": entry.retrieved_on,
        "canonical_id": canonical_id(entry, url),
        "collection": DEFAULT_COLLECTION,
        "source_url": url,
    }


def sitemap_urls(xml: str, limit: int) -> list[str]:
    """Locations from a sitemap, capped. Nested sitemap indexes are skipped."""
    urls = []
    for raw in xml.split("<loc>"):
        loc = raw.split("</loc>", 1)[0].strip()
        if loc.startswith("https://") and not loc.endswith(".xml"):
            urls.append(loc)
        if len(urls) >= limit:
            break
    return urls


async def _page_doc(
    client: httpx.AsyncClient,
    entry: ManifestEntry,
    url: str,
) -> IngestDoc | None:
    """Fetch one page and normalise it; ``None`` when it is not ingestible."""
    try:
        resp = await client.get(url, timeout=60.0)
    except (httpx.HTTPError, OSError) as exc:
        log.warning("bot_docs_fetch_failed url=%s err=%s", url, type(exc).__name__)
        return None
    if resp.status_code != 200 or not resp.content:
        log.warning("bot_docs_fetch_failed url=%s status=%d", url, resp.status_code)
        return None

    ctype = resp.headers.get("content-type", "")
    name = "page.html" if "html" in ctype else url.rsplit("/", 1)[-1] or "page.txt"
    doc = await document_to_ingest_doc(
        DocumentRequest(
            source=SOURCE_NAME,
            doc_id="pending",
            filename=name,
            data=resp.content,
            content_type=ctype,
            title=entry.publisher,
            url=url,
            doc_type=entry.doc_type,
            collection=DEFAULT_COLLECTION,
            extra=_ingest_fields(entry, url),
            settings=_settings_for_pdf() if _is_pdf(url, ctype) else None,
            client=client,
        )
    )
    words = len(doc.content.split())
    if words < MIN_WORDS:
        log.info("bot_docs_too_short url=%s words=%d", url, words)
        return None
    doc_id = f"{canonical_id(entry, url)}:{_hash(doc.content)}"
    metadata = {**doc.metadata, "word_count": words, "doc_id": doc_id}
    return dataclasses.replace(doc, doc_id=doc_id, metadata=metadata)


class BotLaunchpadDocsSource(Source):
    """Bots, launchpads and whitepapers — public documentation pages."""

    name = SOURCE_NAME
    doc_type = "bot_docs"
    default_collection = DEFAULT_COLLECTION
    description = "Telegram bot / launchpad / protocol public docs (manifest-driven)"
    timeout = 60.0

    def __init__(self, category: str | None = None) -> None:
        """Filter the manifest to one category (``bot``/``launchpad``/``whitepaper``)."""
        self.category = (category or "").strip().lower()

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        docs: list[IngestDoc] = []
        seen: set[str] = set()
        entries = [e for e in load_manifest() if not self.category or e.category == self.category]
        for entry in entries:
            urls = [entry.url]
            if entry.mode == "sitemap":
                resp = await client.get(entry.url, timeout=30.0)
                urls = sitemap_urls(resp.text, entry.max_pages) if resp.status_code == 200 else []
                if not urls:
                    log.warning(
                        "bot_docs_sitemap_empty id=%s status=%d", entry.id, resp.status_code
                    )
            for url in urls:
                if limit and len(docs) >= limit:
                    return docs
                doc = await _page_doc(client, entry, url)
                if doc is None or doc.doc_id in seen:
                    continue
                seen.add(doc.doc_id)
                docs.append(doc)
        log.info("bot_docs_fetched docs=%d", len(docs))
        return docs


__all__ = ["SOURCE_NAME", "BotLaunchpadDocsSource", "sitemap_urls"]
