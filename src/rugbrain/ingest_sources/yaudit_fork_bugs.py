"""yAudit/defi-fork-bugs — recurring bugs in forked DeFi protocols.

Source: https://github.com/yAudit/defi-fork-bugs — curated markdown catalog
of recurring bugs in forked DeFi protocols (Compound, Uniswap v2, Balancer,
Curve, Aave forks) with links to official post-mortems / forum threads /
PoCs. The README is structured as ``## <Protocol>`` sections with
``- <description> [Postmortem](url) [POC](url)`` bullets (verified live).

One ``IngestDoc`` per fork-bug entry into ``defi_exploits`` (doc_type
``post_mortem``) — cross-protocol incident knowledge with official links.

Design rules (consistent with ``base.py`` / ``incidents.py``):

  * Fail-open: any fetch/parse failure logs a warning and returns ``[]``.
  * Deterministic ``doc_id`` (``yaudit_defi_fork_bugs:<protocol>:<hash>``)
    keeps re-ingestion idempotent.
  * Credentials: none — raw.githubusercontent content.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "defi_exploits"
DEFAULT_CAP = 2_000
REPO = "yAudit/defi-fork-bugs"
REF = "main"
README_URL = f"https://raw.githubusercontent.com/{REPO}/{REF}/README.md"
SOURCE_URL = f"https://github.com/{REPO}/blob/{REF}/README.md"

_HEADING_RE = re.compile(r"^##\s+(.+)$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_PLAIN_URL_RE = re.compile(r"(?:https?://[^\s)\]]+)")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _content_hash(text: str) -> str:
    """Stable 16-hex content fingerprint (matches ``ingest_common.content_hash``)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ForkBug:
    """One fork-bug entry: protocol, description text, links."""

    protocol: str
    description: str
    links: tuple[tuple[str, str], ...]  # (label, url)


def parse_fork_bugs(markdown: str) -> list[ForkBug]:
    """Parse ``## Protocol`` sections + ``- description [Label](url)`` bullets."""
    bugs: list[ForkBug] = []
    protocol = ""
    for raw in (markdown or "").splitlines():
        line = raw.strip()
        m = _HEADING_RE.match(line)
        if m:
            protocol = m.group(1).strip()
            continue
        if not line.startswith("- ") or not protocol:
            continue
        body = line[2:]
        # Capture links first (labels + urls), then strip them from text.
        links: list[tuple[str, str]] = []
        for lm in _LINK_RE.finditer(body):
            links.append((lm.group(1).strip(), lm.group(2).strip()))
        text = _LINK_RE.sub(" ", body)
        text = re.sub(r"\s+", " ", text).strip()
        # Also keep any bare URLs as (url, url).
        for um in _PLAIN_URL_RE.finditer(text):
            if not any(url == um.group(0) for _, url in links):
                links.append((um.group(0), um.group(0)))
        if not text and not links:
            continue
        bugs.append(ForkBug(protocol=protocol, description=text, links=tuple(links)))
    return bugs


def fork_bug_to_doc(bug: ForkBug, index: int) -> IngestDoc:
    """Normalize one fork-bug entry into an IngestDoc."""
    content_parts = [f"Fork bug in {bug.protocol}: {bug.description or '(no description)'}."]
    for label, url in bug.links:
        content_parts.append(f"{label}: {url}")
    content_parts.append(f"Source: {SOURCE_URL}")

    key = _SLUG_RE.sub("-", bug.protocol.lower()).strip("-") or f"entry-{index}"
    doc_id = f"yaudit_defi_fork_bugs:{key}:{_content_hash(bug.protocol + bug.description)}"
    return IngestDoc(
        content="\n".join(content_parts) + "\n",
        doc_id=doc_id,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "protocol": bug.protocol,
            "category": "fork-bug",
            "severity": "medium",
            "chain": "EVM",
            "ecosystem": "EVM",
            "source_url": SOURCE_URL,
            "links": [url for _, url in bug.links],
            "doc_type": "post_mortem",
        },
        doc_type="post_mortem",
        source="yaudit_defi_fork_bugs",
        title=f"{bug.protocol} — recurring fork bug",
        url=next((url for _, url in bug.links), SOURCE_URL),
    )


class YAuditForkBugsSource(Source):
    """yAudit/defi-fork-bugs — recurring bugs in forked DeFi protocols.

    Emits one ``IngestDoc`` per fork-bug entry into ``defi_exploits``
    (doc_type ``post_mortem``).
    """

    name = "yaudit_defi_fork_bugs"
    doc_type = "post_mortem"
    default_collection = DEFAULT_COLLECTION
    description = (
        "yAudit defi-fork-bugs — recurring fork bugs with post-mortem links (defi_exploits)"
    )
    timeout = 30.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        try:
            r = await client.get(README_URL, timeout=self.timeout)
            markdown = r.text if r.status_code == 200 else ""
        except Exception as e:
            log.warning("yaudit_fetch_error err=%s", str(e)[:160])
            markdown = ""
        bugs = parse_fork_bugs(markdown)
        if not bugs:
            log.warning("yaudit_fetch_empty")
            return []
        log.info("yaudit_feed_ok bugs=%d (cap=%d)", len(bugs), cap)
        return [fork_bug_to_doc(b, i) for i, b in enumerate(bugs[:cap])]


__all__ = [
    "DEFAULT_COLLECTION",
    "ForkBug",
    "YAuditForkBugsSource",
    "fork_bug_to_doc",
    "parse_fork_bugs",
]
