#!/usr/bin/env python3
"""golden_coverage.py — report how many golden rows resolve against the corpus.

A golden row is *covered* when at least one of its ``gold_chunks`` phrases (or
its ``relevant_doc_ids``) is present in the live Qdrant collection it targets.
Rows whose anchors exist nowhere are *orphans* — they score as permanent misses
regardless of retrieval quality, so they must be re-anchored or removed.

COVERED IS NOT RETRIEVED
    Coverage answers "does the anchor exist?" — it does NOT answer "does the
    retriever rank it?". A row can be covered and still miss at top-5, and the
    two failure modes need different fixes:

      * ORPHAN (not covered) — the anchor is absent; re-anchor or drop the row.
      * RANKED-OUT (covered, misses) — the anchor exists but the query's
        vocabulary does not pull it into the top-k. Re-anchoring would be a LIE
        (the chunk is right); the honest fix is to reword the query to the
        corpus's own vocabulary, or accept the miss and record why.

    ``--misses`` prints the known ranked-out rows with their measured rank and
    the diagnosis, so the eval never silently re-anchors a correct chunk to a
    wrong one just to make a number go green.

Usage:
    python3 scripts/golden_coverage.py                 # golden_set.jsonl
    python3 scripts/golden_coverage.py --path <file>   # any golden JSONL
    python3 scripts/golden_coverage.py --misses        # + ranked-out diagnosis
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

# The covered-but-missed rows and their diagnosis live in the package so the
# data is importable (and testable) without running this script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from rugbrain.eval.ranked_out import RANKED_OUT  # noqa: E402


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


def _print_ranked_out() -> None:
    """Print the covered-but-missed rows with their measured rank and cause."""
    print(f"\nranked-out rows (covered, miss at top-5) — {len(RANKED_OUT)}:")
    for query, info in RANKED_OUT.items():
        rank = info["rank"]
        rank_s = f"rank {rank}" if rank is not None else "not in top-500"
        print(f"  - {query!r} [{info['collection']}] {rank_s} — {info['cause']}")
        print(f"      {info['detail']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", type=Path, default=DEFAULT)
    ap.add_argument(
        "--misses",
        action="store_true",
        help="also print the covered-but-ranked-out rows with their diagnosis",
    )
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
    if args.misses:
        _print_ranked_out()
    return 0


if __name__ == "__main__":
    sys.exit(main())
