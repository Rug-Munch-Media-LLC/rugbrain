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

import logging

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, pick_files
from rugbrain.ingest_sources.openzeppelin_docs import (
    AUDIT_DIR,
    DEFAULT_CAP,
    DEFAULT_COLLECTION,
    DOC_TYPE,
    REF,
    REPO,
    _extract_pdf_text,
    _fetch_pdf_text,
    markdown_report_to_doc,
    parse_audit_table,
    pdf_report_to_doc,
)

log = logging.getLogger(__name__)


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
