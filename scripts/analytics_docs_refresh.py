#!/usr/bin/env python3
"""Manifest + refresh for the ``analytics_docs`` corpus.

Three jobs, one entry point, because three scripts would be three definitions of
"what is in this corpus":

  ``--probe``     Re-probe every declared publisher's discovery endpoint and
                  print the observed HTTP status. This is how a ``blocked``
                  publisher becomes ``live`` honestly instead of by assumption.
  ``--manifest``  Read the ingested points back out of Qdrant and write
                  ``data/analytics_docs_manifest.json``: publisher, canonical
                  URL, section count, licence, retrieval date. READ-ONLY.
  ``--refresh``   Run the canonical ingest runner for this one source.

No mode writes to Qdrant directly — ingestion belongs to
``scripts/ingest_all_sources.py`` (chunk → embed 4096 → upsert), and a second
write path is the duplication this repo keeps removing.

Usage
    python scripts/analytics_docs_refresh.py --probe
    python scripts/analytics_docs_refresh.py --manifest
    python scripts/analytics_docs_refresh.py --refresh --limit 400
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from rugbrain.ingest_sources.analytics_docs_targets import PUBLISHERS  # noqa: E402
from rugbrain.qdrant_store import QdrantVectorStore  # noqa: E402

COLLECTION = "analytics_docs"
MANIFEST = ROOT / "data" / "analytics_docs_manifest.json"
UA = "RMI-RagBrain/0.1 (+https://rugmunch.io; source ingestion)"


async def probe() -> int:
    """Print the declared-vs-observed state of every publisher."""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for pub in PUBLISHERS:
            url = pub.llms or pub.sitemap or (pub.seeds[0] if pub.seeds else pub.base)
            try:
                resp = await client.get(url, headers={"User-Agent": UA})
                status = resp.status_code
            except Exception as e:
                status = f"ERR {str(e)[:40]}"
            print(f"{pub.status:8} {status!s:10} {pub.name:16} {url}")
    return 0


async def manifest() -> int:
    """Read ingested points back and write the manifest JSON."""
    store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))
    by_pub: dict[str, dict[str, Any]] = defaultdict(lambda: {"urls": set(), "points": 0})
    offset: str | None = None
    total = 0
    while True:
        points, offset = await store.scroll(COLLECTION, limit=512, offset=offset)
        if not points:
            break
        for p in points:
            payload = p.get("payload") or {}
            pub = str(payload.get("publisher") or "unknown")
            entry = by_pub[pub]
            entry["points"] += 1
            url = payload.get("canonical_id") or payload.get("source_url")
            if url:
                entry["urls"].add(str(url))
            if payload.get("licence"):
                entry["licence"] = payload["licence"]
            if payload.get("retrieved"):
                entry["retrieved"] = payload["retrieved"]
            total += 1
        if not offset:
            break

    declared = {p.name: p for p in PUBLISHERS}
    doc: dict[str, Any] = {
        "collection": COLLECTION,
        "embed_dim": 4096,
        "total_points": total,
        "publishers": {},
        "declared_but_absent": sorted(set(declared) - set(by_pub) - {"arxiv"}),
    }
    for pub, entry in sorted(by_pub.items()):
        decl = declared.get(pub)
        doc["publishers"][pub] = {
            "points": entry["points"],
            "pages": len(entry["urls"]),
            "licence": entry.get("licence") or (decl.licence if decl else None),
            "retrieved": entry.get("retrieved"),
            "status": decl.status if decl else "derived",
            "base": decl.base if decl else None,
            "urls": sorted(entry["urls"])[:500],
        }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"manifest written: {MANIFEST} points={total} publishers={len(by_pub)}")
    print(f"absent: {doc['declared_but_absent']}")
    return 0


def refresh(limit: int | None) -> int:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "ingest_all_sources.py"),
        "--source",
        "analytics_docs",
    ]
    if limit:
        cmd += ["--limit", str(limit)]
    print("running:", " ".join(cmd))
    # argv is built from this script's own path plus an int cap; no shell.
    return subprocess.call(cmd)  # noqa: S603


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe", action="store_true", help="probe declared discovery endpoints")
    ap.add_argument("--manifest", action="store_true", help="write manifest from live Qdrant")
    ap.add_argument("--refresh", action="store_true", help="run the canonical ingest runner")
    ap.add_argument("--limit", type=int, default=None, help="document cap for --refresh")
    args = ap.parse_args()

    if args.probe:
        return asyncio.run(probe())
    if args.manifest:
        return asyncio.run(manifest())
    if args.refresh:
        return refresh(args.limit)
    counts = Counter(p.status for p in PUBLISHERS)
    print(f"declared publishers: {len(PUBLISHERS)} {dict(counts)}")
    print("use --probe, --manifest or --refresh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
