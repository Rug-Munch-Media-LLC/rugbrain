"""Fetch the declared developer-documentation pages into the RAG knowledge lane.

This is the static half of the devdocs ingest; :mod:`rugbrain.ingest_sources.eng_blogs`
is the RSS half. What is fetched is decided entirely by
:data:`~rugbrain.ingest_sources.devdocs_specs.STATIC_SITES` — this module holds no URL of
its own, so there is exactly one answer to "what is in this corpus and under which
licence".

BOUNDED AND FAIL-OPEN
    Every page is one request, each family is capped by its own ``cap``, and the caller's
    ``limit`` truncates again. A 404, a timeout or a consent wall is recorded in the
    returned stats and skipped — it never aborts the batch (the ``base.Source``
    contract). Reporting the failure count is the point: a silently thin run is
    indistinguishable from a source that quietly stopped existing.

    In-run duplicates are dropped by content hash. Cross-run duplication is handled by the
    deterministic ``doc_id`` (``source:slug:contenthash``) surviving into a UUIDv5 point
    id downstream, so a re-run overwrites rather than duplicates.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.devdocs_page import content_hash, doc_from_page, page_to_text
from rugbrain.ingest_sources.devdocs_specs import DOCS_COLLECTION, STATIC_SITES, DocSite

log = logging.getLogger(__name__)

#: Per-request timeout. Doc pages are text; a slow one should not stall the batch.
PAGE_TIMEOUT = 25.0

_RAW_SUFFIXES = (".md", ".rst", ".txt", ".json", ".mdx")


def _is_html(url: str, body: str) -> bool:
    """True when the payload needs stripping.

    Raw doc formats are trusted as-is; anything else is HTML only if it actually looks
    like markup (a JSON error body must not be tag-stripped into an empty string).
    """
    if url.lower().split("?", 1)[0].endswith(_RAW_SUFFIXES):
        return False
    return body.lstrip()[:1] == "<"


async def collect(
    client: httpx.AsyncClient,
    limit: int | None = None,
    sites: tuple[DocSite, ...] = STATIC_SITES,
) -> tuple[list[IngestDoc], dict[str, Any]]:
    """Fetch every declared static site; return ``(docs, stats)``.

    ``stats`` carries ``requested``, ``emitted``, ``skipped_thin``, ``duplicates``,
    ``failed`` and a per-source breakdown, which is what the refresh script prints and
    what makes a thin run visible instead of silent.
    """
    docs: list[IngestDoc] = []
    seen: set[str] = set()
    stats: dict[str, Any] = {
        "requested": 0,
        "emitted": 0,
        "skipped_thin": 0,
        "duplicates": 0,
        "failed": 0,
        "by_source": {},
        "failures": [],
    }
    for site in sites:
        per: dict[str, int] = {"requested": 0, "emitted": 0, "skipped_thin": 0, "failed": 0}
        for url in site.urls[: site.cap]:
            if limit is not None and len(docs) >= limit:
                break
            stats["requested"] += 1
            per["requested"] += 1
            try:
                response = await client.get(url, timeout=PAGE_TIMEOUT)
            except Exception as exc:
                stats["failed"] += 1
                per["failed"] += 1
                stats["failures"].append({"url": url, "error": str(exc)[:160]})
                log.warning("devdocs_fetch_failed url=%s err=%s", url, str(exc)[:160])
                continue
            if response.status_code != 200:
                stats["failed"] += 1
                per["failed"] += 1
                stats["failures"].append({"url": url, "status": response.status_code})
                log.warning("devdocs_non200 url=%s status=%d", url, response.status_code)
                continue
            body = response.text or ""
            is_html = _is_html(url, body)
            # Dedupe on the EXTRACTED BODY, not on the composed content: the composed
            # text carries the page URL, so hashing it would never collapse the two
            # aliases of one page that a doc site happily serves under two paths.
            text = page_to_text(body, is_html=is_html)
            doc = doc_from_page(site, url, body, is_html=is_html)
            if doc is None:
                stats["skipped_thin"] += 1
                per["skipped_thin"] += 1
                continue
            fingerprint = content_hash(text[:4000])
            if fingerprint in seen:
                stats["duplicates"] += 1
                continue
            seen.add(fingerprint)
            docs.append(doc)
            stats["emitted"] += 1
            per["emitted"] += 1
        stats["by_source"][site.name] = per
    return docs, stats


class DevDocsSource(Source):
    """Registered source over :data:`STATIC_SITES` (specs + language docs)."""

    name = "devdocs_static"
    doc_type = "language_docs"
    default_collection = DOCS_COLLECTION
    description = "x402/MCP specs + Python/Rust/Go/Solidity official docs (bounded, fail-open)"

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        docs, _stats = await collect(client, limit=limit)
        return docs


__all__ = ["PAGE_TIMEOUT", "DevDocsSource", "collect"]
