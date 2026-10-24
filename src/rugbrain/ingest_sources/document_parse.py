"""Route a document to Docling, falling back to markitdown.

RugBrain used to hand PDF/DOCX/PPTX/XLSX/HTML/image bytes to markitdown (or to
a pypdf text scrape), which flattens layout: table cells merge, captions drift
and page provenance disappears. When ``DOCLING_ENABLED`` is on and
``DOCLING_URL`` points at a docling-serve, those formats go to Docling first —
markdown plus a structured document — and markitdown stays the fallback for
unsupported formats, oversize payloads and any Docling fault.

Every result records ``parser`` and ``parser_version`` so the corpus can be
audited by extraction engine; a fallback also records why in ``reason``.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from rugbrain.ingest_sources.docling_client import convert_document
from rugbrain.ingest_sources.docling_config import DoclingSettings, settings_from_env

log = logging.getLogger(__name__)

#: Formats Docling understands better than a flat text scrape.
DOCLING_SUFFIXES = frozenset(
    {
        ".pdf", ".docx", ".pptx", ".xlsx",
        ".html", ".htm",
        ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp",
    }
)
PARSER_DOCLING = "docling"
PARSER_MARKITDOWN = "markitdown"
PARSER_TEXT = "text"
MARKITDOWN_VERSION = "unknown"


@dataclass
class ParsedDocument:
    """One extracted document: text plus the provenance of the extraction."""

    markdown: str
    parser: str
    parser_version: str = "unknown"
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def route_to_docling(filename: str, data: bytes, settings: DoclingSettings) -> bool:
    """True when *filename*/*data* should be sent to Docling first."""
    if not settings.usable or not data:
        return False
    if Path(filename).suffix.lower() not in DOCLING_SUFFIXES:
        return False
    return len(data) <= settings.max_bytes


def _markitdown(filename: str, data: bytes) -> tuple[str, str]:
    """Return ``(markdown, version)`` from markitdown, or ``("", "")``."""
    try:
        from markitdown import MarkItDown, StreamInfo
    except ImportError:
        log.warning("markitdown_missing file=%s", filename)
        return "", ""
    try:
        result = MarkItDown().convert_stream(
            io.BytesIO(data), stream_info=StreamInfo(filename=filename)
        )
    except Exception as exc:  # markitdown raises converter-specific errors
        log.warning("markitdown_failed file=%s err=%s", filename, type(exc).__name__)
        return "", ""
    version = getattr(__import__("markitdown"), "__version__", "") or MARKITDOWN_VERSION
    return (result.markdown or "").strip(), str(version)


def _local_markdown(filename: str, data: bytes) -> ParsedDocument:
    """Fallback extraction: markitdown, then a plain text decode."""
    markdown, version = _markitdown(filename, data)
    if markdown:
        return ParsedDocument(
            markdown=markdown,
            parser=PARSER_MARKITDOWN,
            parser_version=version,
            metadata={"source_document": filename},
        )
    text = data.decode("utf-8", errors="replace").strip()
    return ParsedDocument(
        markdown=text,
        parser=PARSER_TEXT,
        parser_version="stdlib",
        reason="" if text else "no_extractable_text",
        metadata={"source_document": filename},
    )


async def parse_document(
    filename: str,
    data: bytes,
    *,
    content_type: str = "",
    settings: DoclingSettings | None = None,
    client: httpx.AsyncClient | None = None,
) -> ParsedDocument:
    """Extract *data* as markdown, preferring Docling for document formats.

    Never raises for an upstream fault: a Docling failure returns the
    markitdown extraction with ``reason`` describing the fault.
    """
    cfg = settings or settings_from_env()
    if not route_to_docling(filename, data, cfg):
        parsed = _local_markdown(filename, data)
        if content_type:
            parsed.metadata["content_type"] = content_type
        return parsed
    result, reason = await convert_document(filename, data, cfg, client)
    if result is not None:
        metadata = dict(result.metadata)
        metadata["content_type"] = content_type or "application/octet-stream"
        return ParsedDocument(
            markdown=result.markdown,
            parser=PARSER_DOCLING,
            parser_version=result.version or "unknown",
            metadata=metadata,
        )
    parsed = _local_markdown(filename, data)
    parsed.reason = reason
    if content_type:
        parsed.metadata["content_type"] = content_type
    log.warning("docling_fallback file=%s reason=%s", filename, reason)
    return parsed


__all__ = [
    "DOCLING_SUFFIXES",
    "PARSER_DOCLING",
    "PARSER_MARKITDOWN",
    "PARSER_TEXT",
    "ParsedDocument",
    "parse_document",
    "route_to_docling",
]
