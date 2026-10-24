"""Per-page normalization for the analytics-docs corpus.

Split out of ``analytics_docs`` so the fetch loop stays a loop and this file
owns the one question that matters for retrieval quality: how a fetched page
becomes a set of ``IngestDoc`` sections.

One document per heading, with the publisher, page title and section name
prepended. The header is not decoration — a bare section body of "id — the
account address" embeds into nothing useful, while "etherscan documentation —
Accounts — Parameters" lands on the question that asked for it.
"""

from __future__ import annotations

import logging
import re

import httpx

from rugbrain.ingest_sources.analytics_docs_pages import section_doc_id, split_sections
from rugbrain.ingest_sources.analytics_docs_targets import Publisher
from rugbrain.ingest_sources.base import USER_AGENT, IngestDoc
from rugbrain.web_crawler import html_to_text

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "analytics_docs"
DOC_TYPE = "analytics_docs"
PAGE_CHAR_CAP = 400_000
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _title(text: str, url: str) -> str:
    m = TITLE_RE.search(text or "")
    if m:
        cleaned = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()
        if cleaned:
            return cleaned[:160]
    return url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")[:160]


async def fetch_page(client: httpx.AsyncClient, url: str) -> str:
    """Page text: markdown as-is, HTML flattened to text."""
    try:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT})
    except Exception as e:
        log.warning("page_fetch_failed url=%s err=%s", url, str(e)[:120])
        return ""
    if resp.status_code != 200:
        log.info("page_status url=%s status=%s", url, resp.status_code)
        return ""
    ctype = resp.headers.get("content-type", "")
    body = resp.text
    if "html" in ctype or body.lstrip().startswith("<"):
        body = html_to_text(body, max_chars=PAGE_CHAR_CAP)
    return body[:PAGE_CHAR_CAP]


def page_docs(pub: Publisher, url: str, text: str, retrieved: str) -> list[IngestDoc]:
    """Normalize one page into one ``IngestDoc`` per section."""
    if not text or len(text.strip()) < 120:
        return []
    title = _title(text, url)
    docs: list[IngestDoc] = []
    for idx, (heading, body) in enumerate(split_sections(text)):
        if len(body.strip()) < 120:
            continue
        header = f"# {pub.name} documentation — {title}"
        if heading:
            header += f" — {heading}"
        content = f"{header}\n\n{body}"
        docs.append(
            IngestDoc(
                content=content,
                doc_id=section_doc_id(pub.name, url, idx, body),
                doc_type=DOC_TYPE,
                source="analytics_docs",
                title=title,
                url=url,
                metadata={
                    "collection": DEFAULT_COLLECTION,
                    "publisher": pub.name,
                    "canonical_id": url,
                    "section": heading or title,
                    "section_index": idx,
                    "licence": pub.licence,
                    "doc_type": DOC_TYPE,
                    "source_url": url,
                    "retrieved": retrieved,
                    "topic": "onchain-analytics",
                },
            )
        )
    return docs
