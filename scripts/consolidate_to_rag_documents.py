"""consolidate_to_rag_documents.py — unify legacy RAG collections into rag_documents.

Target schema (per rmi-backend/app/rag/QDRANT-CONSOLIDATION.md, matching the
live ``rag_documents`` collection):

* point id  = ``uuid5(URL_NAMESPACE, doc_id)``
* doc_id    = ``{source}:{content_type}:{sha256(text)[:16]}``  → content-addressed,
  so identical text upserts in place and duplication is structurally impossible
* vectors   = named ``dense`` (copied from the source — same Qwen3-Embedding-4B
  2560d space, no re-embedding) + ``sparse`` (fastembed BM25 via
  ``rugbrain.qdrant_store._embed_sparse``, same as live ingestion)
* payload   = doc_id, source, content_type, content, collection (legacy
  provenance), chain, address, created_at, updated_at, metadata

Safe by design: COPY only. Legacy collections are untouched until routes are
migrated and counts are verified (``--delete-source`` exists but is opt-in).

Usage:
    python3 scripts/consolidate_to_rag_documents.py --source news_articles --dry-run
    python3 scripts/consolidate_to_rag_documents.py --source news_articles --apply
    python3 scripts/consolidate_to_rag_documents.py --source news_v2 --content-type news --apply
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import urllib.request
import uuid
from typing import Any

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
TARGET = "rag_documents"
BATCH = 200
UUID_NS = uuid.NAMESPACE_URL
TEXT_FIELDS = ("content", "text", "chunk_text", "body", "title", "summary", "description")

#: source collection → default content_type for the unified payload.
CONTENT_TYPE_BY_SOURCE = {
    "news_v2": "news",
    "news_articles": "news",
    "social_intel": "social",
    "rag_documents": "general",
    "scam_intel_v2": "scam",
    "known_scams": "scam",
    "scam_patterns": "scam",
    "wallet_profiles": "wallet",
    "wallet_v2": "wallet",
    "defi_exploits": "exploit",
    "threat_intel_v2": "threat",
    "contract_audits": "audit",
    "github_audits": "audit",
    "crypto_vulnerabilities": "vulnerability",
    "regulation_enforcement": "regulation",
    "research_v2": "research",
    "education": "education",
}


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


def _text_of(payload: dict) -> str:
    for f in TEXT_FIELDS:
        v = payload.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
    md = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    for f in TEXT_FIELDS:
        v = md.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _doc_id(source: str, content_type: str, text: str) -> str:
    return f"{source}:{content_type}:{hashlib.sha256(text.encode()).hexdigest()[:16]}"


def _point_id(doc_id: str) -> str:
    return str(uuid.uuid5(UUID_NS, doc_id))


async def _sparse_for(text: str) -> dict[str, Any] | None:
    try:
        from rugbrain.qdrant_store import _embed_sparse

        sparse = await _embed_sparse(text)
        if sparse is not None:
            return {"indices": sparse[0], "values": sparse[1]}
    except Exception as exc:
        print(f"    sparse_embed_failed: {exc}")
    return None


async def migrate(source: str, content_type: str, apply: bool, limit: int) -> dict[str, int]:
    stats = {"scanned": 0, "skipped_no_text": 0, "upserted": 0}
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
            sparse = await _sparse_for(text)
            vector: dict = {"dense": dense}
            if sparse is not None:  # a None sparse vector makes Qdrant 400 the upsert
                vector["sparse"] = sparse
            out.append(
                {
                    "id": _point_id(did),
                    "vector": vector,
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
            _post(f"/collections/{TARGET}/points?wait=true", {"points": out}, method="PUT")
            stats["upserted"] += len(out)
        else:
            stats["upserted"] += 0
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
    args = ap.parse_args()

    ct = args.content_type or CONTENT_TYPE_BY_SOURCE.get(args.source, "general")
    total = _get(f"/collections/{args.source}")["result"]["points_count"]
    print(
        f"{args.source} ({total:,} points) -> {TARGET} as content_type={ct} ({'APPLY' if args.apply else 'dry run'})"
    )
    stats = asyncio.run(migrate(args.source, ct, args.apply, args.limit))
    print(f"  done: {stats}")
    if not args.apply:
        print("  (dry run — pass --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
