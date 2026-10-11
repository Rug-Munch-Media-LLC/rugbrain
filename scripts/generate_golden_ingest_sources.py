#!/usr/bin/env python3
"""Generate golden set for ingest sources eval (scam_intel_v2_8b, known_scams_8b, threat_intel_v2_8b, news_v2_8b)."""

import json
import os
import sys
import urllib.request
from pathlib import Path

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
OUT = (
    Path(__file__).resolve().parent.parent
    / "eval"
    / "golden"
    / "ingest_sources_golden.jsonl"
)
COLLECTIONS = (
    "scam_intel_v2_8b",
    "known_scams_8b",
    "threat_intel_v2_8b",
    "news_v2_8b",
)
PER_COLLECTION = 7
MIN_CONTENT = 120


def _scroll(collection: str, limit: int) -> list[dict]:
    req = urllib.request.Request(
        f"{QDRANT}/collections/{collection}/points/scroll",
        data=json.dumps({"limit": limit, "with_payload": True, "with_vector": False}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
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


def build() -> list[dict]:
    entries: list[dict] = []
    for collection in COLLECTIONS:
        # Fetch more than needed to filter by content length
        points = _scroll(collection, PER_COLLECTION * 6)
        taken = 0
        for pt in points:
            if taken >= PER_COLLECTION:
                break
            payload = pt.get("payload") or {}
            content = str(payload.get("content") or "")
            if len(content) < MIN_CONTENT:
                continue
            parsed = _title_and_anchor(content)
            if not parsed:
                continue
            query, anchor = parsed
            # Collection name for golden set: we store base name (without _8b) so that
            # when evaluating we can use the same name with the RugBrain API.
            base_collection = collection.replace("_8b", "")
            entries.append(
                {
                    "query": query,
                    "gold_chunks": [anchor],
                    "collection": base_collection,
                    "relevant_doc_ids": [str(pt["id"])],
                }
            )
            taken += 1
    return entries


def main() -> int:
    entries = build()
    print(f"built {len(entries)} anchored queries across {len(COLLECTIONS)} collections")
    for e in entries[:3]:
        print(f"  [{e['collection']}] {e['query'][:60]!r} -> {e['relevant_doc_ids'][0][:20]}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
