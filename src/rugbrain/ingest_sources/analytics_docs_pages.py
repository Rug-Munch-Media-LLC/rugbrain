"""Pure parsers for the analytics-docs corpus: discovery lists and sections.

Nothing here touches the network or Qdrant. Every function takes text and
returns data, so the ingest source stays a thin fetch loop and the sectioning
rules are testable without a live docs site.

Sectioning exists because the shared pipeline chunks on a fixed 2,000-character
window (``ingest_common.chunk_text``). For API documentation that window cuts
through the middle of a parameter table or a code example, which is exactly the
text a retrieval question is about ("what does ``module=account`` accept"). One
document per section means the fixed window operates *inside* a section and a
table or fenced block is never split across two points.

Discovery precedence, and why both lists are parsed rather than one:
``llms.txt`` is curated and complete but only published by Mintlify-family
sites; ``sitemap.xml`` is universal but noisy (tag pages, legal pages, i18n
duplicates). A publisher that has both is read from ``llms.txt`` first.
"""

from __future__ import annotations

import hashlib
import re
from typing import Final
from urllib.parse import urljoin, urlparse

_MD_LINK_RE: Final = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_LOC_RE: Final = re.compile(r"<loc>\s*(https?://[^<\s]+)\s*</loc>", re.IGNORECASE)
_HEADING_RE: Final = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_RE: Final = re.compile(r"^\s*(```|~~~)")

#: Asset and non-document suffixes a sitemap will happily list.
_SKIP_SUFFIXES: Final = (
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".gif",
    ".webp",
    ".ico",
    ".css",
    ".js",
    ".json",
    ".xml",
    ".zip",
    ".pdf",
    ".mp4",
    ".woff",
    ".woff2",
)


def content_hash(text: str) -> str:
    """Stable 16-hex fingerprint, matching ``ingest_common.content_hash``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _clean(url: str) -> str:
    """Drop query/fragment so the same page is one canonical id."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/') or '/'}"


def parse_llms_txt(text: str, base: str = "") -> list[str]:
    """Absolute page URLs from an ``llms.txt`` index, order preserved, deduped."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in _MD_LINK_RE.findall(text or ""):
        absolute = urljoin(base, raw) if base else raw
        if not urlparse(absolute).netloc:
            continue
        url = _clean(absolute)
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def parse_sitemap(text: str) -> tuple[list[str], list[str]]:
    """Split a sitemap into ``(pages, nested_sitemaps)``.

    Nested entries are returned rather than followed so the caller can bound
    recursion; a sitemap index that points at itself must not loop.
    """
    pages: list[str] = []
    nested: list[str] = []
    for raw in _LOC_RE.findall(text or ""):
        url = _clean(raw)
        if url.endswith(".xml"):
            nested.append(url)
        elif not url.lower().endswith(_SKIP_SUFFIXES):
            pages.append(url)
    return pages, nested


def is_page_url(url: str, include: tuple[str, ...] = ()) -> bool:
    """True when *url* looks like a documentation page, not an asset."""
    if not url or url.lower().endswith(_SKIP_SUFFIXES):
        return False
    path = urlparse(url).path or "/"
    return not (include and not any(path.startswith(p) for p in include))


def split_sections(text: str, min_chars: int = 240, max_chars: int = 6000) -> list[tuple[str, str]]:
    """Split markdown-ish *text* on headings into ``(heading, body)`` pairs.

    Fenced blocks are tracked so a ``# comment`` inside a shell example is not
    mistaken for a heading, and a fence is never opened in one section and
    closed in another. Sections shorter than *min_chars* are merged into the
    preceding one (a bare ``## Parameters`` heading carries no answer on its
    own); sections longer than *max_chars* are hard-split, because a single
    oversized point is a silent retrieval miss.
    """
    lines = (text or "").splitlines()
    sections: list[list[str]] = []
    heading = ""
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        chunk = "\n".join(body).strip()
        if heading or chunk:
            sections.append([heading, chunk])

    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            body.append(line)
            continue
        match = None if in_fence else _HEADING_RE.match(line)
        if match:
            flush()
            heading = match.group(2).strip()
            body = []
            continue
        body.append(line)
    flush()

    merged: list[list[str]] = []
    for head, chunk in sections:
        if merged and len(chunk) < min_chars:
            merged[-1][1] = f"{merged[-1][1]}\n\n{head}\n{chunk}".strip()
            continue
        merged.append([head, chunk])

    out: list[tuple[str, str]] = []
    for head, chunk in merged:
        if not chunk:
            continue
        while len(chunk) > max_chars:
            out.append((head, chunk[:max_chars]))
            chunk = chunk[max_chars - 100 :]
            head = f"{head} (continued)"
        out.append((head, chunk))
    return out


def section_doc_id(publisher: str, url: str, index: int, body: str) -> str:
    """Deterministic id: re-ingesting unchanged text upserts, never duplicates."""
    return f"analytics_docs:{publisher}:{_clean(url)}:s{index}:{content_hash(body)}"


__all__ = [
    "content_hash",
    "is_page_url",
    "parse_llms_txt",
    "parse_sitemap",
    "section_doc_id",
    "split_sections",
]
