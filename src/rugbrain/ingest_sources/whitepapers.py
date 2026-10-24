"""Whitepapers source — crypto project whitepapers (KiwisLabs/crypto-whitepapers).

Source: https://github.com/KiwisLabs/crypto-whitepapers — a community
archive of Bitcoin/Ethereum/DeFi whitepapers as markdown and PDF.

Document strategy:

  * ``.md`` whitepapers → full text, doc_type ``whitepaper``.
  * ``.pdf`` whitepapers → full text through ``document_to_ingest_doc``:
    Docling when ``DOCLING_ENABLED`` + ``DOCLING_URL`` are set, markitdown
    otherwise. An unreadable PDF keeps the index stub
    (``metadata.pending_extraction``) so the document stays discoverable.

doc_type: ``whitepaper``.
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.docling_config import DoclingSettings
from rugbrain.ingest_sources.docling_ingest import DocumentRequest, document_to_ingest_doc
from rugbrain.ingest_sources.github import (
    GITHUB_RAW,
    fetch_raw,
    list_repo_files,
    max_size,
    pick_files,
)

log = logging.getLogger(__name__)

REPO = "KiwisLabs/crypto-whitepapers"
REF = "master"
DEFAULT_COLLECTION = "research_v2"
DOC_TYPE = "whitepaper"

# Skip archived non-whitepaper content if present.
EXCLUDE_DIRS = ("assets", "images", "img")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _title_from_path(path: str) -> str:
    name = path.split("/")[-1]
    return re.sub(r"\.(md|pdf)$", "", name, flags=re.I).replace("_", " ").replace("-", " ").strip()


def markdown_to_doc(rel_path: str, markdown: str) -> IngestDoc | None:
    """Normalize one markdown whitepaper into a full-text doc."""
    if not markdown.strip():
        return None
    m = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    title = m.group(1).strip() if m else _title_from_path(rel_path)
    source_url = f"https://github.com/{REPO}/blob/{REF}/{rel_path}"
    return IngestDoc(
        content=markdown.strip(),
        doc_id=f"whitepapers:md:{rel_path}:{_content_hash(markdown[:4000])}",
        doc_type=DOC_TYPE,
        source="crypto_whitepapers",
        title=title,
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "format": "markdown",
            "rel_path": rel_path,
            "source_url": source_url,
            "doc_type": DOC_TYPE,
        },
    )


def pdf_to_stub(rel_path: str, size: int) -> IngestDoc:
    """Index stub for a PDF whitepaper (text extraction not yet enabled)."""
    title = _title_from_path(rel_path)
    source_url = f"https://github.com/{REPO}/blob/{REF}/{rel_path}"
    content = (
        f"Whitepaper (PDF): {title}.\n"
        f"Path: {rel_path}. Size: {size} bytes.\n"
        "Full text extraction requires a PDF library; pending extraction.\n"
    )
    return IngestDoc(
        content=content,
        doc_id=f"whitepapers:pdf:{rel_path}:{_content_hash(rel_path)}",
        doc_type=DOC_TYPE,
        source="crypto_whitepapers",
        title=title,
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "format": "pdf",
            "rel_path": rel_path,
            "size_bytes": size,
            "pending_extraction": True,
            "source_url": source_url,
            "doc_type": DOC_TYPE,
        },
    )


async def _pdf_doc(
    client: httpx.AsyncClient,
    rel_path: str,
    size: int,
    settings: DoclingSettings | None = None,
) -> IngestDoc:
    """Extract a PDF whitepaper (Docling/markitdown) or keep the index stub."""
    source_url = f"https://github.com/{REPO}/blob/{REF}/{rel_path}"
    try:
        resp = await client.get(f"{GITHUB_RAW}/{REPO}/{REF}/{rel_path}", timeout=60.0)
    except (httpx.HTTPError, OSError) as exc:
        log.warning("whitepaper_pdf_failed path=%s err=%s", rel_path, type(exc).__name__)
        return pdf_to_stub(rel_path, size)
    if resp.status_code != 200:
        log.warning("whitepaper_pdf_failed path=%s status=%d", rel_path, resp.status_code)
        return pdf_to_stub(rel_path, size)
    doc = await document_to_ingest_doc(
        DocumentRequest(
            source="crypto_whitepapers",
            doc_id=f"whitepapers:pdf:{rel_path}:{_content_hash(rel_path)}",
            filename=rel_path,
            data=resp.content,
            content_type="application/pdf",
            title=_title_from_path(rel_path),
            url=source_url,
            doc_type=DOC_TYPE,
            collection=DEFAULT_COLLECTION,
            extra={"format": "pdf", "rel_path": rel_path, "source_url": source_url},
            settings=settings,
            client=client,
        )
    )
    if len(doc.content.split()) < 20:
        return pdf_to_stub(rel_path, size)
    return doc


class WhitepapersSource(Source):
    """GitHub crypto whitepapers archive (KiwisLabs/crypto-whitepapers)."""

    name = "crypto_whitepapers"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Crypto project whitepapers (markdown full-text + PDF index stubs)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        entries = await list_repo_files(client, REPO, REF)
        if not entries:
            return []
        md_entries = max_size(
            pick_files(entries, suffixes=(".md",), exclude_dirs=EXCLUDE_DIRS), limit
        )
        pdf_entries = max_size(
            pick_files(entries, suffixes=(".pdf",), exclude_dirs=EXCLUDE_DIRS), limit
        )

        docs: list[IngestDoc] = []
        for e in md_entries:
            path = str(e.get("path", ""))
            markdown = await fetch_raw(client, REPO, REF, path)
            doc = markdown_to_doc(path, markdown)
            if doc:
                docs.append(doc)
            if limit and len(docs) >= limit:
                break
        if limit and len(docs) < limit:
            pdf_entries = pdf_entries[: limit - len(docs)]
        for e in pdf_entries:
            path = str(e.get("path", ""))
            docs.append(await _pdf_doc(client, path, int(e.get("size") or 0)))
            if limit and len(docs) >= limit:
                break
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "REPO",
    "WhitepapersSource",
    "markdown_to_doc",
    "pdf_to_stub",
]
