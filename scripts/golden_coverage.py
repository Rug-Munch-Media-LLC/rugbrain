#!/usr/bin/env python3
"""golden_coverage.py — report how many golden rows resolve against the corpus.

A golden row is *covered* when at least one of its ``gold_chunks`` phrases (or
its ``relevant_doc_ids``) is present in the live Qdrant collection it targets.
Rows whose anchors exist nowhere are *orphans* — they score as permanent misses
regardless of retrieval quality, so they must be re-anchored or removed.

Usage:
    python3 scripts/golden_coverage.py                 # golden_set.jsonl
    python3 scripts/golden_coverage.py --path <file>   # any golden JSONL
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
DEFAULT = Path(__file__).resolve().parent.parent / "src" / "rugbrain" / "eval" / "golden_set.jsonl"
SCROLL = 10000


def _scroll(collection: str, limit: int = SCROLL) -> list[dict]:
    req = urllib.request.Request(  # noqa: S310 — Qdrant http://
        f"{QDRANT}/collections/{collection}/points/scroll",
        data=json.dumps({"limit": limit, "with_payload": True, "with_vector": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — Qdrant http://
            return json.load(resp)["result"]["points"]
    except Exception:
        return []


def _load_entries(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _scroll_cached(collection: str, cache: dict[str, list[dict]]) -> list[dict]:
    if collection not in cache:
        cache[collection] = _scroll(collection + "_8b")
    return cache[collection]


def _entry_covered(entry: dict, points: list[dict]) -> bool:
    contents = [str((p.get("payload") or {}).get("content") or "").lower() for p in points]
    ids = {str(p["id"]) for p in points}
    chunks = [str(c) for c in (entry.get("gold_chunks") or []) if str(c).strip()]
    doc_ids = [str(d) for d in (entry.get("relevant_doc_ids") or []) if str(d).strip()]
    return bool(doc_ids and any(d in ids for d in doc_ids)) or any(
        c.lower() in content for c in chunks for content in contents
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", type=Path, default=DEFAULT)
    args = ap.parse_args()

    entries = _load_entries(args.path)
    cache: dict[str, list[dict]] = {}
    per_collection: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    orphans: list[str] = []

    for entry in entries:
        collection = str(entry.get("collection") or "security_patterns")
        covered = _entry_covered(entry, _scroll_cached(collection, cache))
        per_collection[collection][1] += 1
        if covered:
            per_collection[collection][0] += 1
        else:
            orphans.append(str(entry.get("query") or ""))

    print(f"golden set: {args.path}")
    print(f"rows: {len(entries)}\n")
    total_cov = total = 0
    for collection in sorted(per_collection):
        cov, tot = per_collection[collection]
        total_cov += cov
        total += tot
        print(f"  {collection:24s} {cov:3d}/{tot:<3d} covered")
    print(f"\n  {'TOTAL':24s} {total_cov:3d}/{total:<3d} covered")
    if orphans:
        print(f"\norphans ({len(orphans)}):")
        for query in orphans:
            print(f"  - {query}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
