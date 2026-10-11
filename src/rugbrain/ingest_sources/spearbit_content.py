"""Spearbit public security content source — ``contract_audits`` collection.

Source: https://github.com/spearbit/portfolio
``content/`` directory — Spearbit's public security content: security
checklists, bridge security introductions, and other audit-writing
guides/reviews authored by Spearbit's network of expert auditors.

One ``IngestDoc`` per markdown file into ``contract_audits``
(doc_type ``audit_finding``) — the canonical audit-findings-as-text
collection per ``docs/DATA-PLACEMENT.md``.

Design rules (consistent with ``base.py``):
  * Fetch → normalize → emit; fail-open per file.
  * Deterministic ``doc_id`` (``spearbit_content:<rel_path>``) keeps
    re-ingestion idempotent.
  * Credentials: env-only (``GITHUB_TOKEN`` / ``PROXY_URL``).
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "contract_audits"
REPO = "spearbit/portfolio"
REF = "master"
CONTENT_DIR = "content"
DOC_TYPE = "audit_finding"
DEFAULT_CAP = 50


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


_TITLE_RE = re.compile(r"^#\s+(.+)$")


def _extract_title(markdown: str) -> str:
    """Extract the first H1 title from markdown content."""
    for line in (markdown or "").splitlines():
        m = _TITLE_RE.match(line.strip())
        if m:
            return m.group(1).strip()
    return ""


def spearbit_doc_to_doc(rel_path: str, markdown: str) -> IngestDoc | None:
    """Normalize one Spearbit markdown content file into an IngestDoc."""
    body = (markdown or "").strip()
    if not body or len(body) < 50:
        return None
    title = _extract_title(body) or rel_path.split("/")[-1].removesuffix(".md")
    # Derive the protocol/topic from the directory + filename
    parts = rel_path.split("/")
    topic = parts[-1].removesuffix(".md").replace("_", " ").title() if len(parts) > 1 else title
    source_url = f"https://github.com/{REPO}/blob/{REF}/{rel_path}"
    content = (
        f"Spearbit security content — {title}.\n"
        f"Topic: {topic}\n"
        f"Source: {source_url}\n\n"
        f"{body[:10000]}"
    )
    return IngestDoc(
        content=content,
        doc_id=f"spearbit_content:{rel_path}:{_content_hash(body[:4000])}",
        doc_type=DOC_TYPE,
        source="spearbit_content",
        title=title,
        url=source_url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": topic,
            "audit_team": "Spearbit",
            "vulnerability_class": "audit-methodology",
            "source_url": source_url,
            "rel_path": rel_path,
            "doc_type": DOC_TYPE,
        },
    )


class SpearbitContentSource(Source):
    """spearbit/portfolio ``content/`` — Spearbit security guides/reviews.

    Emits one ``IngestDoc`` per markdown file into ``contract_audits``
    (doc_type ``audit_finding``). PDF slides are skipped (consistent with the
    markdown-only rule used by the SlowMist and Trail of Bits sources).
    """

    name = "spearbit_content"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Spearbit public security content — checklists, guides, reviews (contract_audits)"
    timeout = 30.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        entries = await list_repo_files(client, REPO, REF)
        if not entries:
            log.warning("spearbit_content_tree_failed (rate-limited or repo moved)")
            return []
        md_entries = pick_files(
            entries,
            suffixes=(".md",),
            include=(f"{CONTENT_DIR}/",),
        )
        md_entries = max_size(md_entries, cap)
        docs: list[IngestDoc] = []
        for e in md_entries:
            path = str(e.get("path", ""))
            markdown = await fetch_raw(client, REPO, REF, path)
            doc = spearbit_doc_to_doc(path, markdown)
            if doc:
                docs.append(doc)
            if limit and len(docs) >= cap:
                break
        log.info("spearbit_content_feed_ok docs=%d (cap=%d)", len(docs), cap)
        return docs


__all__ = [
    "DEFAULT_CAP",
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "REF",
    "REPO",
    "SpearbitContentSource",
    "spearbit_doc_to_doc",
]
