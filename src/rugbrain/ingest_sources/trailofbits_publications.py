"""trailofbits/publications — Trail of Bits security review corpus.

Source: https://github.com/trailofbits/publications (default branch
``master``) — ToB's publication archive including the Security Reviews
section (Frax, MobileCoin, Scroll, Uniswap, …). Two text-friendly payloads
are ingested (PDF reports are skipped — consistent with the SlowMist
"markdown only" rule):

  * ``datasets/smart_contract_audit_findings/*.csv`` — structured audit
    findings (initial_findings.csv + other_audit_sources/{chainsecurity,
    consensys}.csv).
  * ``reviews/*.md`` — markdown security-review writeups (e.g.
    ``celo_bugfixes.md``).

One ``IngestDoc`` per finding row / per markdown review into
``contract_audits`` (doc_type ``audit_finding``) — the canonical audit
findings-as-text collection per ``docs/DATA-PLACEMENT.md``.

Design rules (consistent with ``base.py`` / ``github.py``):

  * Fetch → normalize → emit; fail-open per file.
  * Deterministic ``doc_id`` (``trailofbits_publications:<rel_path>[:row]``)
    keeps re-ingestion idempotent.
  * Credentials: env-only (``GITHUB_TOKEN`` / ``PROXY_URL``), never
    hardcoded.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "contract_audits"
DEFAULT_CAP = 2_000
REPO = "trailofbits/publications"
REF = "master"  # verified live on 2026-08-16 (f34aa12)
CSV_DIR = "datasets/smart_contract_audit_findings"
REVIEWS_DIR = "reviews"


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _slug(value: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in value.lower()).strip("-")


def csv_row_to_doc(rel_path: str, headers: list[str], row: list[str], row_index: int) -> IngestDoc:
    """Normalize one CSV finding row into an IngestDoc."""
    fields = {h.strip(): (row[i].strip() if i < len(row) else "") for i, h in enumerate(headers)}
    project = fields.get("Project") or fields.get("project") or ""
    issue = fields.get("Issue") or fields.get("type") or fields.get("Type") or ""
    severity = (
        fields.get("CS Severity") or fields.get("ToB Severity") or fields.get("severity") or ""
    )
    difficulty = fields.get("Difficulty") or fields.get("difficulty") or ""

    # initial_findings.csv rows carry type/severity/difficulty but no project
    # name — they are ordered per-report upstream; fall back to the file name.
    key = _slug(project) or f"row-{row_index}"
    content_parts = [
        f"Trail of Bits audit finding ({rel_path.split('/')[-1]}).",
    ]
    if project:
        content_parts.append(f"Project: {project}")
    if issue:
        content_parts.append(f"Issue: {issue}")
    if severity:
        content_parts.append(f"Severity: {severity}")
    if difficulty:
        content_parts.append(f"Difficulty: {difficulty}")
    content_parts.append(f"Row: {', '.join(f'{k}: {v}' for k, v in fields.items() if v)}")
    content_parts.append(f"Source: https://github.com/{REPO}/blob/{REF}/{rel_path}")

    severity_lower = severity.lower()
    if severity_lower in ("high", "critical", "med"):
        norm_severity = "high" if severity_lower != "med" else "medium"
    elif severity_lower in ("medium", "med"):
        norm_severity = "medium"
    else:
        norm_severity = "low"

    return IngestDoc(
        content="\n".join(content_parts) + "\n",
        doc_id=f"trailofbits_publications:{rel_path}:{key}:{_content_hash(issue + project)}",
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": project or rel_path.split("/")[-1],
            "issue": issue,
            "severity": norm_severity,
            "audit_team": "Trail of Bits",
            "source_url": f"https://github.com/{REPO}/blob/{REF}/{rel_path}",
            "rel_path": rel_path,
            "doc_type": "audit_finding",
        },
        doc_type="audit_finding",
        source="trailofbits_publications",
        title=f"{project or rel_path.split('/')[-1]} — ToB audit finding"
        + (f" ({issue})" if issue else ""),
        url=f"https://github.com/{REPO}/blob/{REF}/{rel_path}",
    )


def parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    """Parse CSV text into ``(headers, rows)``; tolerant of ragged rows."""
    try:
        reader = csv.reader(io.StringIO(text or ""))
        data = [row for row in reader if row and any(cell.strip() for cell in row)]
    except Exception:
        return [], []
    if not data:
        return [], []
    headers = [h.strip() for h in data[0]]
    return headers, data[1:]


def csv_to_docs(rel_path: str, text: str) -> list[IngestDoc]:
    """Normalize one CSV file into per-row audit-finding docs."""
    headers, rows = parse_csv(text)
    docs: list[IngestDoc] = []
    for i, row in enumerate(rows):
        docs.append(csv_row_to_doc(rel_path, headers, row, i))
    return docs


def markdown_review_to_doc(rel_path: str, markdown: str) -> IngestDoc | None:
    """Normalize one ``reviews/*.md`` markdown review into an IngestDoc."""
    body = (markdown or "").strip()
    if not body:
        return None
    name = rel_path.split("/")[-1].removesuffix(".md")
    title_line = next(
        (ln.strip("# ").strip() for ln in body.splitlines() if ln.strip().startswith("#")), ""
    )
    title = title_line or name
    return IngestDoc(
        content=f"Trail of Bits security review — {title}.\n\n{body[:8000]}",
        doc_id=f"trailofbits_publications:{rel_path}",
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": title,
            "severity": "medium",
            "audit_team": "Trail of Bits",
            "source_url": f"https://github.com/{REPO}/blob/{REF}/{rel_path}",
            "rel_path": rel_path,
            "doc_type": "audit_finding",
        },
        doc_type="audit_finding",
        source="trailofbits_publications",
        title=f"{title} — Trail of Bits review",
        url=f"https://github.com/{REPO}/blob/{REF}/{rel_path}",
    )


class TrailOfBitsPublicationsSource(Source):
    """trailofbits/publications — ToB audit findings CSV + markdown reviews.

    Emits one ``IngestDoc`` per finding row / review into ``contract_audits``
    (doc_type ``audit_finding``). PDF reports are skipped.
    """

    name = "trailofbits_publications"
    doc_type = "audit_finding"
    default_collection = DEFAULT_COLLECTION
    description = "Trail of Bits publications — audit findings CSV + markdown security reviews (contract_audits)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        entries = await list_repo_files(client, REPO, REF)
        if not entries:
            log.warning("trailofbits_publications_tree_failed (rate-limited or repo moved)")
            return []

        csv_entries = max_size(
            pick_files(entries, suffixes=(".csv",), include=(f"{CSV_DIR}/",)), cap
        )
        md_entries = max_size(
            pick_files(entries, suffixes=(".md",), include=(f"{REVIEWS_DIR}/",)), cap
        )

        docs: list[IngestDoc] = []
        for e in csv_entries:
            path = str(e.get("path", ""))
            text = await fetch_raw(client, REPO, REF, path)
            docs.extend(csv_to_docs(path, text))
            if len(docs) >= cap:
                break
        if len(docs) < cap:
            for e in md_entries:
                path = str(e.get("path", ""))
                markdown = await fetch_raw(client, REPO, REF, path)
                doc = markdown_review_to_doc(path, markdown)
                if doc:
                    docs.append(doc)
                if len(docs) >= cap:
                    break
        if not docs:
            log.warning("trailofbits_publications_fetch_empty")
        else:
            log.info("trailofbits_publications_feed_ok docs=%d (cap=%d)", len(docs), cap)
        return docs


__all__ = [
    "CSV_DIR",
    "DEFAULT_COLLECTION",
    "REF",
    "REPO",
    "REVIEWS_DIR",
    "TrailOfBitsPublicationsSource",
    "csv_row_to_doc",
    "csv_to_docs",
    "markdown_review_to_doc",
    "parse_csv",
]
