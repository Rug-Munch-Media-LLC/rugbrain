"""Structure metadata extracted from a ``DoclingDocument`` JSON payload.

Docling labels every text item (``title``, ``section_header``, ``caption`` …)
and records page provenance, so headings, captions and page numbers survive
ingestion instead of collapsing into one flat string. Only documented keys are
read; anything missing yields empty values rather than an exception.
"""

from __future__ import annotations

from typing import Any

HEADING_LABELS = frozenset({"title", "section_header"})
CAPTION_LABELS = frozenset({"caption"})
MAX_VALUE_LEN = 300


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [v for v in value.values() if isinstance(v, dict)]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _labelled_texts(document: dict[str, Any], labels: frozenset[str]) -> list[str]:
    out: list[str] = []
    for item in _items(document.get("texts")):
        if item.get("label") not in labels:
            continue
        text = " ".join(str(item.get("text") or "").split())
        if text:
            out.append(text[:MAX_VALUE_LEN])
    return out


def _page_numbers(document: dict[str, Any]) -> list[int]:
    seen: list[int] = []
    for item in _items(document.get("texts")):
        for prov in item.get("prov") or []:
            page = (prov or {}).get("page_no")
            if isinstance(page, int) and page not in seen:
                seen.append(page)
    return sorted(seen)[:200]


def document_metadata(document: dict[str, Any], source: str) -> dict[str, Any]:
    """Return headings/tables/captions/pages/source for *document*."""
    return {
        "headings": _labelled_texts(document, HEADING_LABELS),
        "tables": len(_items(document.get("tables"))),
        "captions": _labelled_texts(document, CAPTION_LABELS),
        "pages": len(_items(document.get("pages"))),
        "page_numbers": _page_numbers(document),
        "source_document": source,
        "docling_schema": str(document.get("schema_name") or ""),
    }


__all__ = ["CAPTION_LABELS", "HEADING_LABELS", "document_metadata"]
