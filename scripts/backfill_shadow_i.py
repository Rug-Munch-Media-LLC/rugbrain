#!/usr/bin/env python3
"""Backfill shadow `_i` collections into mainline (post-flip tool).

WHY: during the v1→v2 embed migration, ingest wrote to 2048-dim shadow
collections (`RAG_INTERIM_COLLECTION_SUFFIX=_i`) while the mainline was being
re-embedded at 2560. After the flip, those shadow docs are orphaned — same
content space, incompatible vector dim.

WHAT (checkpointed state @2026-08-25):
    news_v2_i        ~7264 pts   ← real unique content, MUST backfill
    threat_intel_v2_i  ~147 pts   ← small, backfill
    known_scams_i / scam_patterns_i / defi_exploits_i / others   0 pts → delete

HOW: read each shadow point's ORIGINAL TEXT from payload, re-embed via the
CURRENT backend (post-flip = local 4B @2560), upsert into the mainline
collection with the original id/payload, then delete the shadow collection.

RUN: after the flip, one collection at a time:
    python3 scripts/backfill_shadow_i.py --collection news_v2_i [--dry-run]
Requires: RAG_API_KEY env (or --api-key) against rugbrain-api :8012.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
API = os.getenv("RAG_API_URL", "http://127.0.0.1:8012/api/v1")
BATCH = 32


async def scroll_all(client: httpx.AsyncClient, coll: str):
    offset = None
    while True:
        payload: dict = {"limit": 100, "with_payload": True}
        if offset:
            payload["offset"] = offset
        r = await client.post(f"{QDRANT}/collections/{coll}/points/scroll", json=payload)
        r.raise_for_status()
        res = r.json()["result"]
        for p in res.get("points", []):
            yield p
        offset = res.get("next_page_offset")
        if offset is None:
            return


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", required=True, help="shadow collection, e.g. news_v2_i")
    ap.add_argument("--api-key", default=os.getenv("RMI_RAG_API_KEY", ""))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    target = args.collection.removesuffix("_i")
    api_key = args.api_key.split(",")[0]
    if not api_key:
        print("ERROR: --api-key or RMI_RAG_API_KEY required")
        return 2

    async with httpx.AsyncClient(timeout=120) as client:
        # sanity: shadow exists, mainline exists
        for c in (args.collection, target):
            r = await client.get(f"{QDRANT}/collections/{c}")
            if r.status_code != 200:
                print(f"ERROR: collection {c} missing")
                return 2

        n_ok = n_skip = 0
        async for p in scroll_all(client, args.collection):
            payload = p.get("payload") or {}
            text = payload.get("content") or payload.get("text") or ""
            if not text.strip():
                n_skip += 1
                continue
            if args.dry_run:
                n_ok += 1
                continue
            er = await client.post(
                f"{API}/embed",
                json={"text": text[:8000]},
                headers={"X-API-Key": api_key},
            )
            if er.status_code != 200:
                print(f"embed failed for {p['id']}: HTTP {er.status_code}")
                n_skip += 1
                continue
            vec = er.json().get("embedding") or []
            if len(vec) != 2560:
                print(f"unexpected dim {len(vec)} for {p['id']} — aborting")
                return 2
            ur = await client.put(
                f"{QDRANT}/collections/{target}/points?wait=true",
                json={"points": [{"id": p["id"], "vector": vec, "payload": payload}]},
            )
            if ur.status_code in (200, 201):
                n_ok += 1
            else:
                print(f"upsert failed {p['id']}: {ur.text[:120]}")
                n_skip += 1

    print(
        f"DONE {args.collection} → {target}: reembedded={n_ok} skipped={n_skip}"
        f"{' (dry-run)' if args.dry_run else ''}"
    )
    print("NEXT: verify counts, then DELETE the shadow collection:")
    print(f"  curl -X DELETE {QDRANT}/collections/{args.collection}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
