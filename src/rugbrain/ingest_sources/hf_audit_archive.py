"""gcf3711/audit-report-archive (Hugging Face) — raw audit-report corpus.

Source: https://huggingface.co/datasets/gcf3711/audit-report-archive — a
large archive of public smart-contract audit reports from 22 firms/platforms
(6,153 reports as of July 2026). The reports are PDFs stored in the dataset
repo; the HF datasets-server ``rows`` endpoint serves per-file metadata
(``pdf.src`` signed URL + ``label`` firm classification).

This source wires the corpus pointer so the downstream ingest pipeline can
resolve and extract report text. Each row becomes one ``IngestDoc`` into
``contract_audits`` (doc_type ``audit_finding``) whose content is the label
+ PDF source pointer + size — PDF text extraction is intentionally left to
the store pipeline (same boundary as the rest of this package).

NOTE [REVIEW]: the datasets-server ``rows`` endpoint currently serves only
the split examples (13 rows across train/validation/test) rather than all
6,153 files — the full file list lives in the HF repo itself. The source is
wired best-effort against the rows endpoint; a full backfill would need the
HF repo file listing, which is not implemented here.

Design rules (consistent with ``base.py``):

  * Fail-open: any fetch/parse failure logs a warning and returns ``[]``.
  * Deterministic ``doc_id`` (``gcf3711_audit_report_archive_hf:<hash>``)
    keeps re-ingestion idempotent.
  * Credentials: none — datasets-server is public.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "contract_audits"
DEFAULT_CAP = 500
DATASET = "gcf3711/audit-report-archive"
ROWS_URL = (
    "https://datasets-server.huggingface.co/rows"
    f"?dataset={DATASET}&config=default&split=train&offset=0&length="
)
SOURCE_URL = f"https://huggingface.co/datasets/{DATASET}"


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _extract_rows(data: Any) -> list[dict[str, Any]]:
    """Tolerate the datasets-server ``{"rows": [{"row": {...}}]}`` envelope."""
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("row"), dict):
            out.append(r["row"])
    return out


def row_to_doc(row: dict[str, Any], index: int) -> IngestDoc | None:
    """Normalize one dataset row (pdf src + label) into a corpus-pointer doc."""
    pdf = row.get("pdf") if isinstance(row.get("pdf"), dict) else {}
    src = str(pdf.get("src") or "").strip()
    label = str(row.get("label") or "").strip()
    size = str(pdf.get("size_bytes") or "").strip()
    if not src and not label:
        return None
    content_parts = [
        "Audit report archive entry (gcf3711/audit-report-archive).",
    ]
    if label:
        content_parts.append(f"Firm: {label}")
    if size:
        content_parts.append(f"Size bytes: {size}")
    if src:
        content_parts.append(f"PDF source: {src}")
    return IngestDoc(
        content="\n".join(content_parts) + "\n",
        doc_id=f"gcf3711_audit_report_archive_hf:{_content_hash(src or f'row-{index}:{label}')}",
        metadata={
            "collection": DEFAULT_COLLECTION,
            "audit_team": label,
            "severity": "medium",
            "source_url": SOURCE_URL,
            "pdf_src": src,
            "pdf_size_bytes": size,
            "row_index": index,
            "doc_type": "audit_finding",
        },
        doc_type="audit_finding",
        source="gcf3711_audit_report_archive_hf",
        title=f"Audit report archive — {label or 'report'} ({index + 1})",
        url=SOURCE_URL,
    )


class GCF3711AuditArchiveSource(Source):
    """gcf3711/audit-report-archive (HF) — audit-report corpus pointers.

    Emits one ``IngestDoc`` per dataset row into ``contract_audits``
    (doc_type ``audit_finding``). Best-effort: the rows endpoint currently
    serves split examples, not the full 6,153-report archive.
    """

    name = "gcf3711_audit_report_archive_hf"
    doc_type = "audit_finding"
    default_collection = DEFAULT_COLLECTION
    description = "gcf3711 audit-report-archive (HF) — smart-contract audit report corpus pointers (contract_audits)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        length = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        try:
            r = await client.get(f"{ROWS_URL}{length}", timeout=self.timeout)
            data = r.json() if r.status_code == 200 else {}
        except Exception as e:
            log.warning("gcf3711_fetch_error err=%s", str(e)[:200])
            data = {}
        rows = _extract_rows(data)
        docs = [d for i, row in enumerate(rows) if (d := row_to_doc(row, i)) is not None]
        if not docs:
            log.warning("gcf3711_fetch_empty (datasets-server rows unavailable)")
        else:
            log.info("gcf3711_feed_ok rows=%d", len(docs))
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "GCF3711AuditArchiveSource",
    "_extract_rows",
    "row_to_doc",
]
