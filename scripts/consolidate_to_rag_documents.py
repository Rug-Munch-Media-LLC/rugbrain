"""consolidate_to_rag_documents.py — unify legacy RAG collections into rag_documents.

Target schema (per rmi-backend/app/rag/QDRANT-CONSOLIDATION.md):
  point id = uuid5(URL_NAMESPACE, doc_id); doc_id = {source}:{content_type}:{sha256(text)[:16]}
  vectors  = named ``dense`` (copied as-is, same 2560d space) + ``sparse`` (fastembed BM25)
  payload  = doc_id, source, content_type, content, collection (legacy provenance),
             chain, address, created_at, updated_at, metadata

COPY-only and idempotent: incremental by default (existing ids are skipped, no
re-embedding); ``--full`` re-writes everything. Legacy collections are untouched
until routes are migrated and counts verified.

Usage:
    python3 scripts/consolidate_to_rag_documents.py --source news_v2 --apply
    python3 scripts/consolidate_to_rag_documents.py --source news_v2 --apply --full
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from typing import Any

from rugbrain.consolidation import CONTENT_TYPE_BY_SOURCE
from rugbrain.consolidation import doc_id as _doc_id
from rugbrain.consolidation import point_id as _point_id
from rugbrain.consolidation import text_of as _text_of

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
TARGET = "rag_documents"
BATCH = 200

#: source collection → default content_type for the unified payload.

def _resolve_collection(name: str) -> str:
    """Prefer the live _8b twin (2026-10-08 migration); fall back to the legacy name."""
    if name.endswith("_8b"):
        return name
    try:
        _get(f"/collections/{name}_8b")
        return f"{name}_8b"
    except Exception:  # noqa: BLE001 — a missing twin is the expected fallback path
        return name


def _get(path: str, timeout: int = 60) -> dict:
    with urllib.request.urlopen(f"{QDRANT}{path}", timeout=timeout) as resp:  # noqa: S310 — Qdrant http://
        return json.load(resp)


def _post(path: str, body: dict, timeout: int = 300, method: str = "POST") -> dict:
    req = urllib.request.Request(  # noqa: S310 — Qdrant http://
        f"{QDRANT}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — Qdrant http://
        return json.load(resp)


def _existing_ids(ids: list[str]) -> set[str]:
    """Which of these point ids already exist in the target (incremental sync)."""
    if not ids:
        return set()
    try:
        found = _post(f"/collections/{TARGET}/points", {"ids": ids, "with_payload": False})["result"]
    except Exception:
        return set()
    return {str(pt["id"]) for pt in found}


async def _sparse_for(text: str) -> dict[str, Any] | None:
    try:
        from rugbrain.qdrant_store import _embed_sparse

        sparse = await _embed_sparse(text)
        if sparse is not None:
            return {"indices": sparse[0], "values": sparse[1]}
    except Exception as exc:
        print(f"    sparse_embed_failed: {exc}")
    return None


async def migrate(source: str, content_type: str, apply: bool, limit: int, incremental: bool = True) -> dict[str, int]:
    stats = {"scanned": 0, "skipped_no_text": 0, "skipped_existing": 0, "upserted": 0}
    offset: str | None = None
    t0 = time.time()
    while True:
        body: dict = {"limit": BATCH, "with_payload": True, "with_vector": True}
        if offset is not None:
            body["offset"] = offset
        res = _post(f"/collections/{source}/points/scroll", body)["result"]
        points = res.get("points") or []
        if not points:
            break
        out: list[dict] = []
        for pt in points:
            stats["scanned"] += 1
            payload = pt.get("payload") or {}
            text = _text_of(payload)
            if not text:
                stats["skipped_no_text"] += 1
                continue
            vec = pt.get("vector")
            dense = vec.get("dense") if isinstance(vec, dict) else vec
            if not isinstance(dense, list) or not dense:
                stats["skipped_no_text"] += 1
                continue
            did = _doc_id(source, content_type, text)
            md = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            out.append(
                {
                    "id": _point_id(did),
                    "vector": dense,  # rag_documents uses one UNNAMED 4096-d vector (no sparse)
                    "payload": {
                        "doc_id": did,
                        "source": str(payload.get("source") or source)[:200],
                        "content_type": content_type,
                        "content": text[:20000],
                        "collection": source,
                        "chain": payload.get("chain") or md.get("chain"),
                        "address": payload.get("address") or md.get("address"),
                        "created_at": payload.get("created_at")
                        or md.get("ts")
                        or md.get("published"),
                        "updated_at": payload.get("updated_at")
                        or md.get("ts")
                        or md.get("published"),
                        "metadata": md,
                    },
                }
            )
        if apply and out:
            existing = _existing_ids([p["id"] for p in out]) if incremental else set()
            fresh = [p for p in out if p["id"] not in existing]
            stats["skipped_existing"] += len(out) - len(fresh)
            # NOTE: rag_documents has no sparse vector (single unnamed dense) — do not attach one.
            if fresh:
                _post(f"/collections/{TARGET}/points?wait=true", {"points": fresh}, method="PUT")
            stats["upserted"] += len(fresh)
        if stats["scanned"] % 5000 < BATCH:
            print(
                f"    scanned={stats['scanned']:,} upserted={stats['upserted']:,} ({time.time() - t0:.0f}s)"
            )
        offset = res.get("next_page_offset")
        if offset is None or (limit and stats["scanned"] >= limit):
            break
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--content-type", default="")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--full", action="store_true", help="re-write every point (default: incremental)")
    args = ap.parse_args()

    ct = args.content_type or CONTENT_TYPE_BY_SOURCE.get(args.source, "general")
    source = _resolve_collection(args.source)
    total = _get(f"/collections/{source}")["result"]["points_count"]
    print(
        f"{source} ({total:,} points) -> {TARGET} as content_type={ct} ({'APPLY' if args.apply else 'dry run'})"
    )
    stats = asyncio.run(migrate(source, ct, args.apply, args.limit, incremental=not args.full))
    print(f"  done: {stats}")
    if not args.apply:
        print("  (dry run — pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
