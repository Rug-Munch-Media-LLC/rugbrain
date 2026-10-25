"""arXiv whitepaper half of the analytics-docs corpus.

The four queries are fixed strings in ``analytics_docs_targets.ARXIV_QUERIES``
(clustering, entity resolution, fund tracing, label propagation) and never
built from user input, so a refresh produces the same corpus shape and the
manifest can record exactly what was asked for.

Parsed with ``xml.etree`` from the Atom feed; one ``IngestDoc`` per paper
carrying title + abstract + canonical arXiv URL + published date. Failure is
fail-open like every other source: a dead API returns ``[]`` and the docs half
of the corpus still ingests.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

import httpx

from rugbrain.ingest_sources.analytics_docs_pages import content_hash
from rugbrain.ingest_sources.analytics_docs_targets import (
    ARXIV_API,
    ARXIV_LICENCE,
    ARXIV_QUERIES,
)
from rugbrain.ingest_sources.base import USER_AGENT, IngestDoc

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "analytics_docs"
DOC_TYPE = "analytics_whitepaper"
MAX_RESULTS = 8
_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _entry_text(entry: ET.Element, tag: str) -> str:
    node = entry.find(f"atom:{tag}", _NS)
    return " ".join((node.text or "").split()) if node is not None else ""


async def _query(client: httpx.AsyncClient, query: str, topic: str) -> list[IngestDoc]:
    params = {
        "search_query": query,
        "start": "0",
        "max_results": str(MAX_RESULTS),
        "sortBy": "relevance",
    }
    try:
        resp = await client.get(ARXIV_API, params=params, headers={"User-Agent": USER_AGENT})
    except Exception as e:
        log.warning("arxiv_failed topic=%s err=%s", topic, str(e)[:120])
        return []
    if resp.status_code != 200:
        log.warning("arxiv_status topic=%s status=%s", topic, resp.status_code)
        return []
    try:
        # Fixed arXiv host + Atom feed; no untrusted XML source is accepted.
        root = ET.fromstring(resp.text)  # noqa: S314
    except ET.ParseError as e:
        log.warning("arxiv_parse topic=%s err=%s", topic, str(e)[:120])
        return []

    docs: list[IngestDoc] = []
    for entry in root.findall("atom:entry", _NS):
        title = _entry_text(entry, "title")
        summary = _entry_text(entry, "summary")
        url = _entry_text(entry, "id")
        published = _entry_text(entry, "published")[:10]
        if not title or not summary or not url:
            continue
        body = f"# {title}\n\nTopic: {topic}\nPublished: {published}\nURL: {url}\n\n{summary}"
        docs.append(
            IngestDoc(
                content=body,
                doc_id=f"analytics_docs:arxiv:{content_hash(url + title)}",
                doc_type=DOC_TYPE,
                source="analytics_docs",
                title=title,
                url=url,
                metadata={
                    "collection": DEFAULT_COLLECTION,
                    "publisher": "arxiv",
                    "canonical_id": url,
                    "section": title,
                    "licence": ARXIV_LICENCE,
                    "doc_type": DOC_TYPE,
                    "source_url": url,
                    "retrieved": published,
                    "query": query,
                    "topic": topic,
                },
            )
        )
    return docs


async def arxiv_docs(client: httpx.AsyncClient, retrieved: str) -> list[IngestDoc]:
    """All configured queries, flattened. ``retrieved`` is echoed per doc."""
    docs: list[IngestDoc] = []
    for topic, query in ARXIV_QUERIES:
        for doc in await _query(client, query, topic):
            meta = dict(doc.metadata)
            meta["retrieved"] = retrieved
            docs.append(
                IngestDoc(
                    content=doc.content,
                    doc_id=doc.doc_id,
                    metadata=meta,
                    doc_type=doc.doc_type,
                    source=doc.source,
                    title=doc.title,
                    url=doc.url,
                )
            )
    return docs


__all__ = ["DOC_TYPE", "arxiv_docs"]
