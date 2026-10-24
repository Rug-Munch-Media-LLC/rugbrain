"""Bridge a parsed document into the ``IngestDoc`` ingestion contract.

Sources hand over bytes + identity; this module asks ``parse_document`` for the
markdown and writes the extraction provenance into the document metadata so
search filters can tell Docling output from a markitdown/pypdf scrape:

``parser``, ``parser_version``, ``parser_reason`` (fallback cause), plus
``headings``, ``tables``, ``captions``, ``pages`` and ``page_numbers`` when the
converter supplied them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc
from rugbrain.ingest_sources.docling_config import DoclingSettings
from rugbrain.ingest_sources.document_parse import parse_document


@dataclass(frozen=True)
class DocumentRequest:
    """Everything one document needs to become an ``IngestDoc``."""

    source: str
    doc_id: str
    filename: str
    data: bytes
    content_type: str = ""
    title: str = ""
    url: str = ""
    doc_type: str = ""
    collection: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    settings: DoclingSettings | None = None
    client: httpx.AsyncClient | None = None


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def document_to_ingest_doc(request: DocumentRequest) -> IngestDoc:
    """Parse one document and normalise it into an ``IngestDoc``."""
    parsed = await parse_document(
        request.filename,
        request.data,
        content_type=request.content_type,
        settings=request.settings,
        client=request.client,
    )
    metadata: dict[str, Any] = {
        "parser": parsed.parser,
        "parser_version": parsed.parser_version,
        "source_document": request.filename,
    }
    if parsed.reason:
        metadata["parser_reason"] = parsed.reason
    metadata.update({k: v for k, v in parsed.metadata.items() if k != "source_document"})
    if request.collection:
        metadata["collection"] = request.collection
    if request.doc_type:
        metadata["doc_type"] = request.doc_type
    metadata.update(request.extra)
    doc_id = request.doc_id or (
        f"{request.source}:{request.filename}:{_content_hash(parsed.markdown)}"
    )
    return IngestDoc(
        content=parsed.markdown,
        doc_id=doc_id,
        metadata=metadata,
        doc_type=request.doc_type,
        source=request.source,
        title=request.title or request.filename,
        url=request.url,
    )


__all__ = ["DocumentRequest", "document_to_ingest_doc"]
