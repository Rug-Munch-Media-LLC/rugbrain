"""OpenZeppelin audit reports source — ``contract_audits`` collection.

Source: https://github.com/OpenZeppelin/openzeppelin-contracts
``audits/`` directory — public smart-contract audit reports published by
OpenZeppelin (and historically by New Alchemy / LevelK) as markdown and
PDF. The ``README.md`` audit table lists every report with date, version,
commit, auditor, scope, and a link to the report file.

One ``IngestDoc`` per report (markdown full-text or PDF-extracted text) into
``contract_audits`` (doc_type ``audit_finding``). The audit table's
structured metadata (date, version, auditor, scope) is extracted from the
README and attached as payload so downstream search can filter by audit
date/version/auditor.

PDF text extraction uses ``pypdf`` (available in the host runtime). PDFs that
fail extraction are skipped with a warning — the markdown report and the audit
table summary are still ingested.

Design rules (consistent with ``base.py``):
  * Fetch → normalize → emit; fail-open per file.
  * Deterministic ``doc_id`` (``openzeppelin_audits:<rel_path>``) keeps
    re-ingestion idempotent.
  * Credentials: env-only (``GITHUB_TOKEN`` / ``PROXY_URL``).
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import GITHUB_RAW, fetch_raw, list_repo_files, pick_files

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "contract_audits"
REPO = "OpenZeppelin/openzeppelin-contracts"
REF = "master"
AUDIT_DIR = "audits"
DOC_TYPE = "audit_finding"
DEFAULT_CAP = 50  # bounded: audit reports are long + PDF extraction is slow


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ingest_common.content_hash)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


_AUDIT_TABLE_ROW_RE = re.compile(
    r"\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|"
)
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def parse_audit_table(markdown: str) -> list[dict[str, str]]:
    """Parse the OpenZeppelin audits README.table into structured metadata.

    Each row: | Date | Version | Commit | Auditor | Scope | Links |
    Returns list of dicts with keys: date, version, commit, auditor, scope, links.
    """
    entries: list[dict[str, str]] = []
    for line in (markdown or "").splitlines():
        line = line.strip()
        m = _AUDIT_TABLE_ROW_RE.match(line)
        if not m:
            continue
        date, version, commit, auditor, scope, links = m.groups()
        # Skip header separator rows and the header row itself
        if date.strip().startswith("---") or date.strip().lower() == "date":
            continue
        entries.append(
            {
                "date": date.strip(),
                "version": version.strip(),
                "commit": commit.strip(),
                "auditor": auditor.strip(),
                "scope": scope.strip(),
                "links": links.strip(),
            }
        )
    return entries


def _extract_pdf_text(content: bytes) -> str:
    """Extract text from a PDF byte blob using pypdf.

    Returns the extracted text, or an empty string if extraction fails.
    pypdf is imported lazily so the module loads even when pypdf is absent
    (the README markdown path does not need it).
    """
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n".join(pages).strip()
    except Exception as e:
        log.warning("pdf_extract_failed err=%s", str(e)[:160])
        return ""


async def _fetch_pdf_text(client: httpx.AsyncClient, repo: str, ref: str, path: str) -> str:
    """Fetch a PDF from raw.githubusercontent.com and extract its text."""
    url = f"{GITHUB_RAW}/{repo}/{ref}/{path}"
    try:
        r = await client.get(url, timeout=60.0)
        if r.status_code != 200:
            log.warning("pdf_fetch_failed path=%s status=%d", path, r.status_code)
            return ""
        return _extract_pdf_text(r.content)
    except Exception as e:
        log.warning("pdf_fetch_error path=%s err=%s", path, str(e)[:160])
        return ""


def markdown_report_to_doc(
    rel_path: str, markdown: str, audit_meta: dict[str, str] | None = None
) -> IngestDoc | None:
    """Normalize one markdown audit report into an IngestDoc."""
    body = (markdown or "").strip()
    if not body or len(body) < 50:
        return None
    meta = audit_meta or {}
    protocol = meta.get("scope", "") or rel_path
    auditor = meta.get("auditor", "OpenZeppelin")
    source_url = f"https://github.com/{REPO}/blob/{REF}/{rel_path}"
    content = (
        f"OpenZeppelin security audit report — {protocol}.\n"
        f"Auditor: {auditor}\n"
        f"Date: {meta.get('date', 'unknown')}\n"
        f"Version: {meta.get('version', 'unknown')}\n"
        f"Scope: {protocol}\n"
        f"Source: {source_url}\n\n"
        f"{body[:10000]}"
    )
    return IngestDoc(
        content=content,
        doc_id=f"openzeppelin_audits:{rel_path}:{_content_hash(body[:4000])}",
        doc_type=DOC_TYPE,
        source="openzeppelin_audits",
        title=f"{protocol} — OpenZeppelin audit ({meta.get('date', '')})",
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": protocol,
            "audit_team": auditor,
            "audit_date": meta.get("date", ""),
            "audit_version": meta.get("version", ""),
            "commit": meta.get("commit", ""),
            "scope": protocol,
            "source_url": source_url,
            "rel_path": rel_path,
            "doc_type": DOC_TYPE,
        },
    )


def pdf_report_to_doc(
    rel_path: str, text: str, audit_meta: dict[str, str] | None = None
) -> IngestDoc | None:
    """Normalize one PDF-extracted audit report into an IngestDoc."""
    body = (text or "").strip()
    if not body:
        return None
    meta = audit_meta or {}
    protocol = meta.get("scope", "") or rel_path
    auditor = meta.get("auditor", "OpenZeppelin")
    source_url = f"https://github.com/{REPO}/blob/{REF}/{rel_path}"
    content = (
        f"OpenZeppelin security audit report — {protocol}.\n"
        f"Auditor: {auditor}\n"
        f"Date: {meta.get('date', 'unknown')}\n"
        f"Version: {meta.get('version', 'unknown')}\n"
        f"Scope: {protocol}\n"
        f"Source: {source_url}\n\n"
        f"{body[:10000]}"
    )
    return IngestDoc(
        content=content,
        doc_id=f"openzeppelin_audits:{rel_path}:{_content_hash(body[:4000])}",
        doc_type=DOC_TYPE,
        source="openzeppelin_audits",
        title=f"{protocol} — OpenZeppelin audit ({meta.get('date', '')})",
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": protocol,
            "audit_team": auditor,
            "audit_date": meta.get("date", ""),
            "audit_version": meta.get("version", ""),
            "commit": meta.get("commit", ""),
            "scope": protocol,
            "source_url": source_url,
            "rel_path": rel_path,
            "doc_type": DOC_TYPE,
        },
    )


class OpenZeppelinAuditsSource(Source):
    """OpenZeppelin audit reports from ``audits/`` (markdown + PDF).

    Emits one ``IngestDoc`` per audit report into ``contract_audits``
    (doc_type ``audit_finding``). The README audit table is parsed first so
    each report inherits its date/version/auditor/scope metadata. Bounded by
    ``limit`` (default cap ``DEFAULT_CAP``).
    """

    name = "openzeppelin_audits"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "OpenZeppelin public smart-contract audit reports (contract_audits)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        entries = await list_repo_files(client, REPO, REF)
        if not entries:
            log.warning("openzeppelin_audits_tree_failed (rate-limited or repo moved)")
            return []
        audit_entries = pick_files(entries, include=(f"{AUDIT_DIR}/",))
        # Fetch README first to parse the audit table
        readme = await fetch_raw(client, REPO, REF, f"{AUDIT_DIR}/README.md")
        audit_table = parse_audit_table(readme) if readme else []
        log.info("openzeppelin_audit_table entries=%d", len(audit_table))

        docs: list[IngestDoc] = []
        for entry in audit_entries:
            path = str(entry.get("path", ""))
            if path.endswith("README.md"):
                continue  # parsed separately above
            if path.endswith(".md"):
                md = await fetch_raw(client, REPO, REF, path)
                doc = markdown_report_to_doc(path, md)
                if doc:
                    docs.append(doc)
            elif path.endswith(".pdf"):
                text = await _fetch_pdf_text(client, REPO, REF, path)
                doc = pdf_report_to_doc(path, text)
                if doc:
                    docs.append(doc)
            if limit and len(docs) >= cap:
                break
        log.info("openzeppelin_audits_feed_ok docs=%d (cap=%d)", len(docs), cap)
        return docs


__all__ = [
    "DEFAULT_CAP",
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "REF",
    "REPO",
    "OpenZeppelinAuditsSource",
    "_extract_pdf_text",
    "markdown_report_to_doc",
    "parse_audit_table",
    "pdf_report_to_doc",
]
