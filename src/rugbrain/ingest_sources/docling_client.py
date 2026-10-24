"""HTTP adapter for a docling-serve instance (``DOCLING_URL``).

The single place that speaks the service contract:
``POST {base}/v1/convert/file`` with a multipart ``files`` field and repeated
``to_formats`` fields, answering
``{"document": {"md_content", "json_content"}, "status", "errors"}``.

Fail-open contract: any transport error, timeout, 4xx/5xx or unusable payload
returns ``(None, reason)``. The caller keeps the local extractor and records
``parser_reason`` — ingestion never aborts because Docling is down.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from rugbrain.ingest_sources.docling_config import DoclingSettings
from rugbrain.ingest_sources.docling_metadata import document_metadata

ENDPOINT = "/v1/convert/file"

log = logging.getLogger(__name__)


@dataclass
class DoclingResult:
    """Converted markdown plus the structure metadata we keep on the doc."""

    markdown: str
    version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _form_fields(settings: DoclingSettings) -> dict[str, Any]:
    """Multipart form fields; list values become repeated keys.

    httpx 0.28 rejects a list-of-tuples ``data`` payload on an AsyncClient
    ("Attempted to send an sync request"), so repeated fields are expressed as
    dict values instead.
    """
    return {
        "to_formats": ["md", "json"],
        "image_export_mode": "placeholder",
        "do_table_structure": "true",
        "table_mode": settings.table_mode,
        "do_ocr": "true",
        "abort_on_error": "false",
    }


def _parse_payload(payload: dict[str, Any], filename: str) -> tuple[DoclingResult | None, str]:
    document = payload.get("document")
    document = document if isinstance(document, dict) else {}
    markdown = str(document.get("md_content") or "").strip()
    status = str(payload.get("status") or "")
    if not markdown:
        return None, f"docling_empty:{status or 'no_md'}"
    json_content = document.get("json_content")
    json_content = json_content if isinstance(json_content, dict) else {}
    metadata = document_metadata(json_content, source=filename)
    metadata["docling_status"] = status or "success"
    errors = payload.get("errors") or []
    if errors:
        metadata["docling_errors"] = [str(e)[:200] for e in errors[:5]]
    version = str(json_content.get("version") or "").strip()
    return DoclingResult(markdown=markdown, version=version, metadata=metadata), ""


async def convert_document(
    filename: str,
    data: bytes,
    settings: DoclingSettings,
    client: httpx.AsyncClient | None = None,
) -> tuple[DoclingResult | None, str]:
    """Convert one document through docling-serve.

    Returns ``(result, "")`` on success or ``(None, reason)`` where *reason* is
    a short stable token (``docling_http_503``, ``docling_timeout`` …) that is
    stored in the document's ``parser_reason`` metadata.
    """
    if not settings.usable:
        return None, "docling_disabled"
    if len(data) > settings.max_bytes:
        return None, "docling_size_cap"
    owned = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(settings.timeout, connect=10.0)
    )
    try:
        resp = await http.post(
            f"{settings.url}{ENDPOINT}",
            files={"files": (filename, data, "application/octet-stream")},
            data=_form_fields(settings),
        )
    except httpx.TimeoutException:
        log.warning("docling_timeout file=%s", filename)
        return None, "docling_timeout"
    except (httpx.HTTPError, OSError) as exc:
        log.warning("docling_transport file=%s err=%s", filename, type(exc).__name__)
        return None, f"docling_transport:{type(exc).__name__}"
    finally:
        if owned:
            await http.aclose()
    if resp.status_code >= 400:
        log.warning("docling_http file=%s status=%d", filename, resp.status_code)
        return None, f"docling_http_{resp.status_code}"
    try:
        payload = resp.json()
    except ValueError:
        log.warning("docling_bad_json file=%s", filename)
        return None, "docling_bad_json"
    if not isinstance(payload, dict):
        return None, "docling_bad_payload"
    result, reason = _parse_payload(payload, filename)
    if result is not None and not result.version:
        result.version = str(resp.headers.get("x-docling-version") or "").strip()
    return result, reason


__all__ = ["DoclingResult", "convert_document"]
