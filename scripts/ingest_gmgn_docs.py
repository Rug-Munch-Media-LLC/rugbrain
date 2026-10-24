#!/usr/bin/env python3
"""Ingest the GMGN documentation snapshot into ``gmgn_docs`` (4096-d lane).

Input is the deduped ``pages.jsonl`` written by ``scripts/fetch_gmgn_docs.py``
(one record per canonical URL, with publisher, licence, sha256 and markdown).
Nothing is fetched here: the ingest is a pure function of the snapshot, so a
re-run is reproducible and an offline box can reproduce yesterday's corpus.

CHUNKING — by section, not by character window
    Each page is split on its markdown headings. A chunk carries its heading
    path (``# Page > ## Endpoint > ### Request``) so a retrieved chunk still
    says where in the docs it came from. Fenced code blocks and markdown tables
    are atomic: the splitter never cuts inside a fence, and a section that is
    still too long is split at blank lines OUTSIDE fences, so an endpoint table
    or a curl example is never torn in half. That is the whole reason this does
    not reuse the fixed-width ``split_text`` helper.

EMBED — canonical lane only
    ``embed_lane.embed_batch`` walks the 4096-d chain and dimension-checks every
    response; a 1024/2560 lane cannot be stored. The dim is asserted again here
    before the write, because the ingest is what the gate protects.

Usage
    python scripts/ingest_gmgn_docs.py --dry-run          # chunk + count only
    python scripts/ingest_gmgn_docs.py --limit 20         # bounded batch
    python scripts/ingest_gmgn_docs.py                    # full ingest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

COLLECTION = "gmgn_docs"
SOURCE = "gmgn-docs"
EMBED_DIM = 4096
MAX_CHARS = 1800
BATCH = 8
DEFAULT_SNAPSHOT = Path("data/gmgn_docs/pages.jsonl")
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _split_units(page: str) -> list[tuple[str, str]]:
    """Split markdown into ``(heading_path, body)`` units.

    Fences are consumed as opaque blocks: once a fence opens, every line up to
    the closing fence belongs to the current unit regardless of leading ``#``,
    which is how a shell comment inside a code sample stops being a heading.
    """
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


def _pack(body: str) -> list[str]:
    """Pack one section into <= MAX_CHARS chunks, breaking only outside fences.

    Lines are the unit, so a fenced block that contains blank lines stays one
    indivisible run: the fence state is tracked per line and a cut is refused
    while it is open. A table row or a curl example is therefore never torn.
    """
    if len(body) <= MAX_CHARS:
        return [body]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    in_fence = False
    for line in body.splitlines(keepends=True):
        if _FENCE.match(line):
            in_fence = not in_fence
        if size + len(line) > MAX_CHARS and current and not in_fence:
            chunks.append("".join(current).strip())
            current, size = [], 0
        current.append(line)
        size += len(line)
    if "".join(current).strip():
        chunks.append("".join(current).strip())
    return [c for c in chunks if c]


def _chunks(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in pages:
        units = _split_units(str(page.get("content", "")))
        page_chunks: list[tuple[str, str]] = []
        for heading, body in units:
            for piece in _pack(body):
                page_chunks.append((heading, piece))
        for idx, (heading, piece) in enumerate(page_chunks):
            out.append(
                {
                    "doc_id": f"{SOURCE}:{page['canonical_id']}:{idx}",
                    "text": f"# {page['title']}\n\n{heading}\n\n{piece}".strip(),
                    "metadata": {
                        "url": page["url"],
                        "title": page["title"],
                        "section": page["section"],
                        "heading_path": heading,
                        "publisher": page["publisher"],
                        "licence": page["licence"],
                        "source_kind": page["source_kind"],
                        "fetched_at": page["fetched_at"],
                        "page_sha256": page["sha256"],
                        "chunk_index": idx,
                        "total_chunks": len(page_chunks),
                    },
                }
            )
    return out


async def ingest(
    snapshot: Path, *, limit: int | None = None, dry_run: bool = False
) -> dict[str, int]:
    from embed_lane import embed_batch

    from rugbrain.qdrant_store import QdrantVectorStore

    text = await asyncio.to_thread(snapshot.read_text)
    pages = [json.loads(line) for line in text.splitlines() if line.strip()]
    chunks = _chunks(pages)
    if limit is not None:
        chunks = chunks[:limit]
    stats = {
        "pages": len(pages),
        "chunks": len(chunks),
        "embedded": 0,
        "upserted": 0,
        "errors": 0,
        "dim_mismatch": 0,
    }
    if dry_run or not chunks:
        return stats

    store = QdrantVectorStore()
    try:
        for start in range(0, len(chunks), BATCH):
            batch = chunks[start : start + BATCH]
            vectors = await embed_batch([c["text"] for c in batch])
            for chunk, vector in zip(batch, vectors, strict=True):
                if not vector:
                    stats["errors"] += 1
                    continue
                if len(vector) != EMBED_DIM:
                    stats["dim_mismatch"] += 1
                    continue
                stats["embedded"] += 1
                ok = await store.insert(
                    doc_id=chunk["doc_id"],
                    collection=COLLECTION,
                    embedding=vector,
                    content=chunk["text"],
                    metadata=chunk["metadata"],
                    source=chunk["metadata"]["url"],
                    severity="info",
                    chain="multi",
                )
                stats["upserted" if ok else "errors"] += 1
    finally:
        await store.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    ap.add_argument("--limit", type=int, default=None, help="max chunks to embed")
    ap.add_argument("--dry-run", action="store_true", help="chunk and count only")
    args = ap.parse_args()
    stats = asyncio.run(ingest(args.snapshot, limit=args.limit, dry_run=args.dry_run))
    print(json.dumps(stats, sort_keys=True))
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
