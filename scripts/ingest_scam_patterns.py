#!/usr/bin/env python3
"""Ingest aggregated scam-pattern signatures onto the 4096-dim (8B) lane.

Extraction is already implemented and deterministic in
``rugbrain.scam_patterns.extract_scam_patterns_from_docs``; this script is
only the missing CALLER plus a safe write path. Nothing is extracted here.

Safety contract (operator rule: nothing is overwritten or deleted):
  * Default is --dry-run. Writes require an explicit --write.
  * Target is a NEW collection (default ``scam_pattern_signatures_8b``).
    The existing ``scam_patterns`` collection holds 7,102 cryptoscamdb
    PER-VICTIM records; this output is AGGREGATED patterns. Different
    meanings, so they must not share a collection, and this script never
    deletes, truncates or overwrites an existing collection.
  * Point ids are deterministic (uuid5 of the pattern id), so a re-run
    upserts in place instead of duplicating.
  * Vectors are 4096-dim Qwen/Qwen3-Embedding-8B only. A dim other than
    CANARY_DIM is refused rather than written.

Writes go through the raw Qdrant HTTP API rather than
``QdrantVectorStore.insert`` on purpose: that method is gated on
``_RMI_RAG_OWNED_COLLECTIONS`` (collections.py, not this lane's to edit)
and its embedder is pinned to the 2560-dim 4B lane. The 8B lane is
default-off there by design, so this lane must not borrow it.

Usage:
    python scripts/ingest_scam_patterns.py                     # dry run
    python scripts/ingest_scam_patterns.py --payload-only p.json
    python scripts/ingest_scam_patterns.py --write --limit 3000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import uuid
from typing import Any

import httpx

from rugbrain.canary_routing import CANARY_DIM, CANARY_MODEL
from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.scam_patterns import (
    THREAT_INTEL_COLLECTION,
    extract_scam_patterns_from_docs,
)

logger = logging.getLogger("ingest_scam_patterns")

from rugbrain.dim_drift import DEFAULT_QDRANT_URL
QDRANT_URL = os.getenv("QDRANT_URL", DEFAULT_QDRANT_URL).rstrip("/")
KEY_FILES = (
    os.getenv("DEEPINFRA_KEY_FILE", ""),
    "/srv/secrets/deepinfra_api_key",
    "/run/secrets/deepinfra_api_key",
)
# NEW collection. The 8B collections are _8b-suffixed mirrors of a base
# collection; there is no base here, so the name carries the lane explicitly
# and states what it holds.
DEFAULT_COLLECTION = "scam_pattern_signatures_8b"
POINT_ID_PREFIX = "scam_pattern_signature"
SOURCE = "scam_pattern_extractor"


def _read_key() -> str:
    """DeepInfra key from env, then the fleet secret paths. Never logged."""
    value = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if value:
        return value
    for path in KEY_FILES:
        if not path:
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError:
            continue
        if value:
            return value
    return ""


def pattern_point_id(pattern_id: str) -> str:
    """Deterministic uuid5 point id so a re-run upserts, never duplicates."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{POINT_ID_PREFIX}:{pattern_id}"))


def render_text(pattern: dict[str, Any]) -> str:
    """Flatten one pattern into the text that gets embedded."""
    return (
        f"SCAM PATTERN SIGNATURE: {pattern['attack_type']} ({pattern['technique']})\n"
        f"Protocols: {', '.join(pattern['affected_protocols'][:10])}\n"
        f"Chain: {pattern['chain']} | Severity: {pattern['severity']}\n"
        f"Funds lost: ${pattern['funds_lost']:,.0f} | Incidents: {pattern['doc_count']}\n"
        f"Indicators: {', '.join(pattern['indicators'][:12])}\n"
        f"Reference incidents: {', '.join(pattern['reference_incidents'][:5])}"
    )


def _payload(pattern: dict[str, Any], *, indicators_complete: bool) -> dict[str, Any]:
    """Qdrant payload for one pattern.

    ``indicators_complete`` records whether the deterministic keyword scan
    actually found indicators. Several groups have an EMPTY indicators list
    while the technique itself is real (the technique then falls back to the
    attack vector), so an empty list must never read as "no indicators here".
    """
    return {
        "content": render_text(pattern),
        "metadata": {
            "pattern_id": pattern["pattern_id"],
            "attack_type": pattern["attack_type"],
            "technique": pattern["technique"],
            "severity": pattern["severity"],
            "chain": pattern["chain"],
            "funds_lost": pattern["funds_lost"],
            "doc_count": pattern["doc_count"],
            "affected_protocols": pattern["affected_protocols"][:20],
            "reference_incidents": pattern["reference_incidents"][:20],
            "indicators": pattern["indicators"][:20],
            "indicators_complete": indicators_complete,
            "vector_lane": f"{CANARY_MODEL}@{CANARY_DIM}",
        },
        "source": SOURCE,
        "severity": pattern["severity"],
        "chain": pattern["chain"],
        "collection": DEFAULT_COLLECTION,
    }


async def _read_pool(pool: str, limit: int) -> list[dict[str, Any]]:
    """Scroll *limit* docs from *pool* (read-only; never creates anything)."""
    store = QdrantVectorStore()
    docs: list[dict[str, Any]] = []
    offset: str | None = None
    try:
        while len(docs) < limit:
            batch, offset = await store.scroll(
                pool, limit=min(500, limit - len(docs)), offset=offset
            )
            if not batch:
                break
            docs.extend(batch)
            if not offset:
                break
    finally:
        await store.close()
    return docs


async def _embed_8b(texts: list[str]) -> list[list[float]]:
    """Embed on the 4096 lane. Raises on any dim mismatch: no silent 2560."""
    key = _read_key()
    if not key:
        raise RuntimeError("no DeepInfra key: set DEEPINFRA_API_KEY or DEEPINFRA_KEY_FILE")
    url = os.getenv("RAG_8B_EMBED_URL", "https://api.deepinfra.com/v1/embeddings")
    payload = {"model": os.getenv("RAG_8B_EMBED_MODEL", CANARY_MODEL), "input": texts}
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    items = sorted(r.json().get("data", []), key=lambda i: i.get("index", 0))
    vectors = [[float(x) for x in item.get("embedding", [])] for item in items]
    if len(vectors) != len(texts) or any(len(v) != CANARY_DIM for v in vectors):
        raise RuntimeError(f"embed dim mismatch: expected {CANARY_DIM}")
    return vectors


async def _write_patterns(
    patterns: list[dict[str, Any]], collection: str, batch: int = 64
) -> tuple[int, int]:
    """Create *collection* at 4096 and upsert every pattern. Returns (stored, failed)."""
    texts = [render_text(p) for p in patterns]
    vectors = await _embed_8b(texts)
    stored = failed = 0
    async with httpx.AsyncClient(timeout=120.0) as client:
        exists = await client.get(f"{QDRANT_URL}/collections/{collection}")
        if exists.status_code == 404:
            r = await client.put(
                f"{QDRANT_URL}/collections/{collection}",
                json={"vectors": {"size": CANARY_DIM, "distance": "Cosine"}},
            )
            r.raise_for_status()
            logger.info("created collection=%s dim=%d", collection, CANARY_DIM)
        points = [
            {
                "id": pattern_point_id(p["pattern_id"]),
                "vector": v,
                "payload": _payload(p, indicators_complete=bool(p["indicators"])),
            }
            for p, v in zip(patterns, vectors, strict=True)
        ]
        for start in range(0, len(points), batch):
            r = await client.put(
                f"{QDRANT_URL}/collections/{collection}/points",
                json={"points": points[start : start + batch]},
            )
            if r.status_code in (200, 201):
                stored += len(points[start : start + batch])
            else:
                failed += len(points[start : start + batch])
                logger.error("upsert_failed status=%d body=%s", r.status_code, r.text[:200])
    return stored, failed


async def main() -> int:
    """CLI entry point: read the pool, extract patterns, report or write."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pool", default=THREAT_INTEL_COLLECTION, help="source collection")
    ap.add_argument("--collection", default=DEFAULT_COLLECTION, help="target collection")
    ap.add_argument("--limit", type=int, default=3000, help="max docs to read")
    ap.add_argument("--write", dest="dry_run", action="store_false", help="actually write")
    ap.add_argument("--payload-only", metavar="PATH", help="write patterns as JSON, no Qdrant")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )

    docs = await _read_pool(args.pool, args.limit)
    patterns = extract_scam_patterns_from_docs(docs)
    empty = [p for p in patterns if not p["indicators"]]
    logger.info(
        "read=%d patterns=%d empty_indicators=%d lane=%s@%d",
        len(docs),
        len(patterns),
        len(empty),
        CANARY_MODEL,
        CANARY_DIM,
    )
    for p in patterns[:10]:
        logger.info(
            "  %-52s $%-12s docs=%-4d incidents=%d indicators=%d",
            p["pattern_id"],
            f"{p['funds_lost']:,.0f}",
            p["doc_count"],
            len(p["reference_incidents"]),
            len(p["indicators"]),
        )

    if args.payload_only:
        with open(args.payload_only, "w", encoding="utf-8") as handle:  # noqa: ASYNC230
            json.dump(patterns, handle, indent=2)
        logger.info("wrote %d patterns to %s (no Qdrant)", len(patterns), args.payload_only)
        return 0

    if args.dry_run:
        logger.info(
            "DRY RUN — nothing written. Target would be %s @ %d dim.", args.collection, CANARY_DIM
        )
        logger.info("Re-run with --write to create/upsert it.")
        return 0

    stored, failed = await _write_patterns(patterns, args.collection)
    logger.info("stored=%d failed=%d collection=%s", stored, failed, args.collection)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
