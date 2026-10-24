#!/usr/bin/env python3
"""Ingest finding-level security corpora into ``security_findings_8b`` / ``audit_reports_8b``.

Input is the manifest source ``public_audit_findings`` (see
``rugbrain/ingest_sources/audit_findings.py``): one doc per public repo file,
already carrying ``url``, ``publisher``, ``licence``, ``date``, ``commit_sha``
and ``doc_type``. Nothing is fetched from a page here, so a re-run is a pure
function of the pinned commit.

CHUNKING — by FINDING, not by character window
    A finding is the unit an auditor reasons over: ``H-01 title`` followed by
    Impact / Likelihood / Proof of Concept / Recommendation. Splitting fixed
    windows tears an impact statement away from its PoC and produces two
    useless chunks. So the splitter keys on finding headings (``H-01``,
    ``M-03``, ``C-01``, ``Critical``, ``High``…), keeps the preamble of the
    report as its own context chunk, and never cuts inside a fenced code block:
    a PoC or a vulnerable snippet stays whole, and an over-long finding is
    split at line boundaries OUTSIDE fences.

EMBED — canonical lane only
    ``embed_lane.embed_batch`` walks the 4096-d chain (SiliconFlow ``.com``
    first) and dimension-checks every response, so a 1024/2560 lane cannot be
    stored. The width is asserted again before the write: an ingest that
    "succeeds" while storing nothing is the failure this repo has already paid
    for once.

Usage
    python scripts/ingest_security_knowledge.py --dry-run
    python scripts/ingest_security_knowledge.py --limit 1200 --repo SunWeb3Sec/DeFiVulnLabs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.ingest_sources.audit_chunking import _pack, _severity_of, _units

EMBED_DIM = 4096
BATCH = 8
SOURCE = "public_audit_findings"
def _chunks(docs: list[Any]) -> list[dict[str, Any]]:
    """One record per finding chunk, carrying the provenance the audit needs."""
    out: list[dict[str, Any]] = []
    for doc in docs:
        # ``url``/``doc_type`` are IngestDoc FIELDS, not payload keys; the
        # source keeps both (a pinned blob URL and the semantic type there, a
        # filterable copy in metadata). Read the field, fall back to metadata.
        md = doc.metadata
        url = getattr(doc, "url", "") or md.get("url", "")
        header = f"{md['publisher']} · {md['repo']}/{md['path']} · {md['doc_type']} · commit {md['commit_sha'][:12]}"
        for fid, title, body in _units(doc.content):
            for idx, piece in enumerate(_pack(body)):
                text = f"{header}\n\n{fid} {title}\n\n{piece}".strip()
                out.append(
                    {
                        "doc_id": f"{SOURCE}:{md['repo']}:{md['path']}:{fid}:{idx}",
                        "text": text,
                        "severity": _severity_of(title) if title else "info",
                        "metadata": {
                            **{k: md[k] for k in ("publisher", "licence", "repo", "path",
                                                  "commit_sha", "date", "doc_type", "collection")},
                            "url": url,
                            "source": SOURCE,
                            "finding_id": fid,
                            "finding_title": title,
                            "chunk_index": idx,
                        },
                    }
                )
    return out


async def ingest(limit: int | None, repos: tuple[str, ...], dry_run: bool) -> dict[str, int]:
    from embed_lane import embed_batch

    from rugbrain.ingest_sources.audit_findings import PublicAuditFindingsSource
    from rugbrain.qdrant_store import QdrantVectorStore

    docs = await PublicAuditFindingsSource(repos=repos).fetch()
    chunks = _chunks(docs)
    if limit is not None:
        chunks = chunks[:limit]
    stats = {"docs": len(docs), "chunks": len(chunks), "embedded": 0,
             "upserted": 0, "errors": 0, "dim_mismatch": 0}
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
                    collection=chunk["metadata"]["collection"] + "_8b",
                    embedding=vector,
                    content=chunk["text"],
                    metadata=chunk["metadata"],
                    source=chunk["metadata"]["url"],
                    severity=chunk["severity"],
                    chain="multi",
                )
                stats["upserted" if ok else "errors"] += 1
    finally:
        await store.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None, help="max finding chunks to embed")
    ap.add_argument("--repo", action="append", default=[], help="manifest repo (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    stats = asyncio.run(ingest(args.limit, tuple(args.repo), args.dry_run))
    print(json.dumps(stats, sort_keys=True))
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
