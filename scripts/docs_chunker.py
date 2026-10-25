#!/usr/bin/env python3
"""Section-aware markdown chunker shared by the docs ingest pipelines.

WHY A MODULE AND NOT TWO COPIES
    ``ingest_gmgn_docs`` and ``ingest_gamblefi_docs`` both consume a
    ``pages.jsonl`` snapshot of documentation pages and need the identical
    rule: split on headings, never cut inside a fenced block, never tear a
    markdown table from its header. Two copies would drift the day one of them
    learns about a new fence style, and the corpus would then hold two
    competing definitions of "a chunk". So the rule lives here once.

THE RULE
    * A chunk carries its heading path (``# Page > ## Endpoint > ### Request``)
      so a retrieved chunk still says where in the docs it came from.
    * Fences (``` / ~~~) are opaque: every line up to the closing fence belongs
      to the current unit, so a ``# comment`` inside a shell sample is not a
      heading.
    * A section longer than ``max_chars`` is packed by LINES, and a cut is
      refused while a fence is open, so a curl example or an ABI block is never
      torn in half.

Nothing here embeds or writes. ``chunk_pages`` is a pure function of the
snapshot, which is what makes an offline re-run reproduce yesterday's corpus.
"""

from __future__ import annotations

import re
from typing import Any, Final

MAX_CHARS: Final = 1800
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def split_units(page: str) -> list[tuple[str, str]]:
    """Split markdown into ``(heading_path, body)`` units."""
    path: list[str] = []
    units: list[tuple[str, list[str]]] = [("", [])]
    in_fence = False
    for line in page.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            units[-1][1].append(line)
            continue
        m = None if in_fence else _HEADING.match(line)
        if m:
            level = len(m.group(1))
            path = [*path[: level - 1], m.group(2).strip()]
            units.append((" > ".join(path), []))
            continue
        units[-1][1].append(line)
    return [(h, "\n".join(b).strip()) for h, b in units if "\n".join(b).strip()]


def pack(body: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Pack one section into chunks <= ``max_chars``, breaking only outside fences."""
    if len(body) <= max_chars:
        return [body]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    in_fence = False
    for line in body.splitlines(keepends=True):
        if _FENCE.match(line):
            in_fence = not in_fence
        if size + len(line) > max_chars and current and not in_fence:
            chunks.append("".join(current).strip())
            current, size = [], 0
        current.append(line)
        size += len(line)
    if "".join(current).strip():
        chunks.append("".join(current).strip())
    return [c for c in chunks if c]


def chunk_pages(pages: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """Build embeddable chunks: ``doc_id``, ``text`` and the provenance metadata."""
    out: list[dict[str, Any]] = []
    for page in pages:
        page_chunks: list[tuple[str, str]] = []
        for heading, body in split_units(str(page.get("content", ""))):
            page_chunks.extend((heading, piece) for piece in pack(body))
        for idx, (heading, piece) in enumerate(page_chunks):
            out.append(
                {
                    "doc_id": f"{source}:{page['canonical_id']}:{idx}",
                    "text": f"# {page['title']}\n\n{heading}\n\n{piece}".strip(),
                    "metadata": {
                        "url": page["url"],
                        "title": page["title"],
                        "section": page.get("section", ""),
                        "heading_path": heading,
                        "publisher": page.get("publisher", ""),
                        "licence": page.get("licence", ""),
                        "source_kind": page.get("source_kind", ""),
                        "fetched_at": page.get("fetched_at", ""),
                        "page_sha256": page.get("sha256", ""),
                        "chunk_index": idx,
                        "total_chunks": len(page_chunks),
                    },
                }
            )
    return out


__all__ = ["MAX_CHARS", "chunk_pages", "pack", "split_units"]
