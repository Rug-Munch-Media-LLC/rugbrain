"""EIPs + BIPs standards sources (ethereum/EIPs, bitcoin/bips).

doc_type: ``standard`` — Ethereum Improvement Proposals and Bitcoin
Improvement Proposals, the canonical protocol-standards documentation.

  * EIPs are markdown files ``EIPS/eip-<n>.md`` with YAML front-matter
    (``title``, ``status``, ``type``, ``category``, ``created``).
  * BIPs are ``bip-<n>.mediawiki`` (some ``.md``) with a ``bip: <n>`` /
    ``Title:`` header block.

Both normalize to the same document shape; ``metadata`` carries the
front-matter/header fields so downstream filtering can target e.g. only
``status=Final`` standards.
"""

from __future__ import annotations

import hashlib
import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source
from rugbrain.ingest_sources.github import fetch_raw, list_repo_files, max_size, pick_files

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "standards"
DOC_TYPE = "standard"

EIPS_REPO = "ethereum/EIPs"
EIPS_REF = "master"
BIPS_REPO = "bitcoin/bips"
BIPS_REF = "master"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADER_LINE_RE = re.compile(r"^\s*([A-Za-z][\w ]*?)\s*:\s*(.*?)\s*$")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def parse_frontmatter(markdown: str) -> tuple[dict[str, str], str]:
    """Split YAML front-matter from body. Returns (fields, body)."""
    m = _FRONTMATTER_RE.match(markdown or "")
    if not m:
        return {}, markdown or ""
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip().strip("'\"")
    return fields, markdown[m.end() :]


def parse_header_block(text: str) -> tuple[dict[str, str], str]:
    """Parse a ``key: value`` header block (BIP mediawiki style)."""
    fields: dict[str, str] = {}
    body_lines: list[str] = []
    in_header = True
    for raw in (text or "").splitlines():
        m = _HEADER_LINE_RE.match(raw)
        if (
            in_header
            and m
            and m.group(1).lower()
            in (
                "bip",
                "title",
                "author",
                "status",
                "type",
                "created",
                "license",
            )
        ):
            fields[m.group(1).lower()] = m.group(2).strip()
        else:
            in_header = False
            body_lines.append(raw)
    return fields, "\n".join(body_lines)


def standard_to_doc(
    *,
    prefix: str,
    number: str,
    title: str,
    body: str,
    source: str,
    fields: dict[str, str],
    url: str,
) -> IngestDoc:
    """Normalize a standard (EIP/BIP) into an IngestDoc."""
    title = title.strip() or f"{prefix}-{number}"
    status = fields.get("status", "")
    std_type = fields.get("type", "")
    created = fields.get("created", "")
    parts = [f"{prefix}-{number} — {title}. Protocol standard."]
    if status:
        parts.append(f"Status: {status}.")
    if std_type:
        parts.append(f"Type: {std_type}.")
    if created:
        parts.append(f"Created: {created}.")
    if fields.get("category"):
        parts.append(f"Category: {fields['category']}.")
    content = " ".join(parts) + "\n\n" + body.strip()
    return IngestDoc(
        content=content[:200_000],
        doc_id=f"{source}:{prefix.lower()}-{number}:{_content_hash(body[:4000])}",
        doc_type=DOC_TYPE,
        source=source,
        title=f"{prefix}-{number}: {title}",
        url=url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "standard_id": f"{prefix}-{number}",
            "status": status,
            "type": std_type,
            "category": fields.get("category", ""),
            "created": created,
            "doc_type": DOC_TYPE,
        },
    )


class EIPsSource(Source):
    """Ethereum Improvement Proposals (ethereum/EIPs)."""

    name = "eips"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Ethereum Improvement Proposals (EIP/ERC standards)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        entries = await list_repo_files(client, EIPS_REPO, EIPS_REF)
        eip_entries = pick_files(entries, suffixes=(".md",), include=("EIPS/eip-",))
        eip_entries = max_size(eip_entries, limit)
        docs: list[IngestDoc] = []
        for e in eip_entries:
            path = str(e.get("path", ""))
            markdown = await fetch_raw(client, EIPS_REPO, EIPS_REF, path)
            if not markdown.strip():
                continue
            fields, body = parse_frontmatter(markdown)
            number = re.sub(r"\D", "", path.split("/")[-1])
            title = fields.get("title", "")
            if not title:
                m = re.search(r"^#+\s+(.+)$", body, re.MULTILINE)
                title = m.group(1).strip() if m else ""
            docs.append(
                standard_to_doc(
                    prefix="EIP",
                    number=number,
                    title=title,
                    body=body,
                    source=self.name,
                    fields=fields,
                    url=f"https://github.com/{EIPS_REPO}/blob/{EIPS_REF}/{path}",
                )
            )
            if limit and len(docs) >= limit:
                break
        return docs


class BIPsSource(Source):
    """Bitcoin Improvement Proposals (bitcoin/bips)."""

    name = "bips"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Bitcoin Improvement Proposals (BIP standards)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        entries = await list_repo_files(client, BIPS_REPO, BIPS_REF)
        bip_entries = pick_files(entries, suffixes=(".mediawiki", ".md"), include=("bip-",))
        bip_entries = max_size(bip_entries, limit)
        docs: list[IngestDoc] = []
        for e in bip_entries:
            path = str(e.get("path", ""))
            raw = await fetch_raw(client, BIPS_REPO, BIPS_REF, path)
            if not raw.strip():
                continue
            fields, body = parse_header_block(raw)
            number = re.sub(r"\D", "", path.split("/")[-1])
            title = fields.get("title", "")
            if not title:
                m = re.search(r"^==+\s*(.+?)\s*==+", body, re.MULTILINE)
                title = m.group(1).strip() if m else ""
            docs.append(
                standard_to_doc(
                    prefix="BIP",
                    number=number,
                    title=title,
                    body=body,
                    source=self.name,
                    fields=fields,
                    url=f"https://github.com/{BIPS_REPO}/blob/{BIPS_REF}/{path}",
                )
            )
            if limit and len(docs) >= limit:
                break
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "BIPsSource",
    "EIPsSource",
    "parse_frontmatter",
    "parse_header_block",
    "standard_to_doc",
]
