"""build_golden_set.py — build a content-anchored golden set for RAG backtesting.

The hand-written golden set drifted into mislabels ("reentrancy guard pattern
solidity" had gold ``VulnerableVyper`` while retrieval returned the exact
OpenZeppelin mutex chunk) — an unmaintainable ground truth scores perfect
retrieval as failure.

This generator anchors ground truth to the corpus itself: sample chunks from
target collections, use the chunk's own title/first line as the query, and its
point id as the gold. That makes the set *pipeline-validating* (must pass:
proves ingest -> embed -> search -> id-match end to end) and cheap to rebuild
after every re-ingest. Harder, human-curated queries belong on top, not
instead.

Usage:
    python3 scripts/build_golden_set.py --per-collection 10 --write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
OUT = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "rugbrain"
    / "eval"
    / "golden"
    / "crypto_rag_golden.jsonl"
)
COLLECTIONS = (
    "security_patterns",
    "defi_exploits",
    "threat_intel_v2",
    "rag_documents",
    "education",
)
MIN_CONTENT = 120


def _scroll(collection: str, limit: int) -> list[dict]:
    req = urllib.request.Request(  # noqa: S310 — Qdrant http://
        f"{QDRANT}/collections/{collection}/points/scroll",
        data=json.dumps({"limit": limit, "with_payload": True, "with_vector": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — Qdrant http://
        return json.load(resp)["result"]["points"]


def _title_and_anchor(content: str) -> tuple[str, str] | None:
    """Query = first line (title-like); anchor = a distinctive phrase inside."""
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    if not lines:
        return None
    query = lines[0][:120]
    if len(query) < 12:
        return None
    body = " ".join(lines[1:]) if len(lines) > 1 else ""
    anchor = " ".join(body.split()[:8]) if len(body.split()) >= 4 else query
    return query, anchor


def build(per_collection: int) -> list[dict]:
    entries: list[dict] = []
    for collection in COLLECTIONS:
        points = _scroll(collection, per_collection * 6)
        taken = 0
        for pt in points:
            if taken >= per_collection:
                break
            content = str((pt.get("payload") or {}).get("content") or "")
            if len(content) < MIN_CONTENT:
                continue
            parsed = _title_and_anchor(content)
            if not parsed:
                continue
            query, anchor = parsed
            entries.append(
                {
                    "query": query,
                    "gold_chunks": [anchor],
                    "collection": collection,
                    "relevant_doc_ids": [str(pt["id"])],
                }
            )
            taken += 1
    return entries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-collection", type=int, default=10)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    entries = build(args.per_collection)
    print(f"built {len(entries)} anchored queries across {len(COLLECTIONS)} collections")
    for e in entries[:3]:
        print(f"  [{e['collection']}] {e['query'][:60]!r} -> {e['relevant_doc_ids'][0][:20]}")
    if args.write:
        backup = OUT.with_suffix(".jsonl.bak")
        if OUT.exists():
            backup.write_text(OUT.read_text())
        OUT.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")
        print(f"wrote {OUT} (backup: {backup.name})")
    else:
        print("(dry run — pass --write to replace the golden set)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
