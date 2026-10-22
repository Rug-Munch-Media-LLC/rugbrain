"""reanchor_golden.py — re-resolve golden-set doc ids against the live corpus.

The golden set carries ``relevant_doc_ids`` from an older ingest generation;
after re-ingestion the point ids changed, so exact-id matching fails and the
eval reports near-zero recall even when retrieval is visibly correct (e.g.
"reentrancy guard pattern solidity" returned the exact OpenZeppelin mutex
chunk, scored a miss).

This re-anchors each query's gold ids by finding, inside the target
collection, the point whose content contains one of the query's
``gold_chunks``. Queries whose anchors are gone are reported as unresolved
(never silently dropped).

Usage:
    python3 scripts/reanchor_golden.py --dry-run     # report (default)
    python3 scripts/reanchor_golden.py --write       # rewrite the golden set
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
#: collections that were consolidated away -> where their content lives now.
SUCCESSORS = {"coding_knowledge": "codebase_reference_v2", "solana_patterns": "codebase_reference_v2"}
GOLDEN = Path(__file__).resolve().parent.parent / "src" / "rugbrain" / "eval" / "golden" / "crypto_rag_golden.jsonl"
SCROLL = 4000


def _scroll(collection: str, limit: int = SCROLL) -> list[dict]:
    req = urllib.request.Request(  # noqa: S310 — Qdrant http://
        f"{QDRANT}/collections/{collection}/points/scroll",
        data=json.dumps({"limit": limit, "with_payload": True, "with_vector": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — Qdrant http://
            return json.load(resp)["result"]["points"]
    except urllib.error.HTTPError as exc:  # collection gone (consolidated) -> empty, reported
        print(f"  (collection {collection} unavailable: HTTP {exc.code})")
        return []


def _uses_named_vectors(collection: str) -> bool:
    """True when the collection stores named vectors (dense + sparse)."""
    try:
        with urllib.request.urlopen(f"{QDRANT}/collections/{collection}", timeout=30) as resp:  # noqa: S310
            vectors = json.load(resp)["result"]["config"]["params"].get("vectors") or {}
        return isinstance(vectors, dict) and "size" not in vectors
    except Exception:  # noqa: BLE001 — fall back to unnamed
        return False


def search_anchor(collection: str, anchor: str, limit: int = 5) -> str | None:
    """Vector-search a big collection for the anchor text; return the top hit id."""
    import asyncio

    from rugbrain.query_embedder import embed_query

    vec = asyncio.run(embed_query(anchor))
    named = _uses_named_vectors(collection)
    vector: object = {"name": "dense", "vector": vec} if named else vec
    req = urllib.request.Request(  # noqa: S310 — Qdrant http://
        f"{QDRANT}/collections/{collection}/points/search",
        data=json.dumps({"vector": vector, "limit": limit, "with_payload": True}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — Qdrant http://
            hits = json.load(resp)["result"]
    except urllib.error.HTTPError:
        return None
    for hit in hits:
        content = str((hit.get("payload") or {}).get("content") or "").lower()
        if anchor.lower() in content:
            return str(hit["id"])
    return str(hits[0]["id"]) if hits else None


def reanchor(entry: dict, points: list[dict]) -> str | None:
    """Return the current point id whose content contains a gold anchor."""
    anchors = [str(a).lower() for a in (entry.get("gold_chunks") or []) if str(a).strip()]
    if not anchors:
        return None
    for pt in points:
        content = str((pt.get("payload") or {}).get("content") or "").lower()
        if any(a in content for a in anchors):
            return str(pt["id"])
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="rewrite the golden set (default: report)")
    args = ap.parse_args()

    lines = GOLDEN.read_text().splitlines()
    entries = [json.loads(line) for line in lines if line.strip() and not line.startswith("#")]
    cache: dict[str, list[dict]] = {}
    resolved = unresolved = 0

    out_entries = []
    for entry in entries:
        collection = SUCCESSORS.get(entry.get("collection") or "", entry.get("collection") or "security_patterns")
        entry["collection"] = collection
        if collection not in cache:
            cache[collection] = _scroll(collection)
        anchors = [str(a) for a in (entry.get("gold_chunks") or []) if str(a).strip()]
        if cache[collection]:
            new_id = reanchor(entry, cache[collection])
        elif anchors:
            new_id = search_anchor(collection, anchors[0])
        else:
            new_id = None
        if new_id is None and anchors:
            new_id = search_anchor(collection, anchors[0])
        if new_id is None:
            unresolved += 1
            print(f"  UNRESOLVED  [{collection}] {str(entry.get('query'))[:60]}")
        elif new_id == (entry.get("relevant_doc_ids") or [None])[0]:
            resolved += 1
        else:
            resolved += 1
            entry["relevant_doc_ids"] = [new_id]
            print(f"  re-anchored [{collection}] {str(entry.get('query'))[:50]}")
        out_entries.append(entry)

    print(f"\n{len(entries)} queries: {resolved} resolved, {unresolved} unresolved")
    if args.write:
        backup = GOLDEN.with_suffix(".jsonl.bak")
        backup.write_text(GOLDEN.read_text())
        GOLDEN.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in out_entries) + "\n")
        print(f"wrote {GOLDEN} (backup: {backup.name})")
    else:
        print("(dry run — pass --write to update)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
