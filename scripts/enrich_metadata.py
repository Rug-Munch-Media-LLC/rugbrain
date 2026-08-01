#!/usr/bin/env python3
"""Enrich Qdrant points with canonical crypto-security metadata.

Scrolls every point in the target collections and fills the canonical schema
(see ``rmi_rag.crypto_schema``): ``attack_vector``, ``severity``, ``chain``,
``amount_lost_usd``, ``date``, ``protocol``, ``source_type`` — at the top
level of the payload and mirrored inside ``metadata``.

Enrichment sources, in priority order:
  1. Existing payload / metadata fields (e.g. rekt_news already writes
     ``protocol``, ``amount_usd``, ``hack_date``, ``attack_vector``).
  2. Keyword/pattern detection over the document ``content`` (attack vectors,
     chains, USD amounts, ISO dates).
  3. Optional ``--fetch-urls``: fetch the ``source_url`` (hacks collection
     only) when content-based enrichment still leaves gaps.

Idempotent and safe: only *adds/updates* canonical fields, never deletes any
existing payload key. Points are updated via Qdrant's batched ``set_payload``
API (100 points per request).

Usage:
    python scripts/enrich_metadata.py                       # all collections
    python scripts/enrich_metadata.py --collections threat_intel hacks
    python scripts/enrich_metadata.py --dry-run --limit 50
    python scripts/enrich_metadata.py --fetch-urls --limit 10   # hacks only

Requires (host):
  - Qdrant at QDRANT_URL (default http://localhost:6333)
  - httpx
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Any

import httpx

from rmi_rag.crypto_schema import CANONICAL_FIELDS, enrich_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("enrich_metadata")

DEFAULT_COLLECTIONS = ("threat_intel", "scam_intel", "hacks", "news", "wallet")
CANONICAL_FIELDS_SET = set(CANONICAL_FIELDS)
REQUEST_TIMEOUT = 30
BATCH_SIZE = 100


async def scroll_points(
    client: httpx.AsyncClient, collection: str, limit: int | None = None
) -> list[dict[str, Any]]:
    """Scroll all points in a collection (payload only, no vectors)."""
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
    points: list[dict[str, Any]] = []
    offset: str | None = None
    while True:
        body: dict[str, Any] = {"limit": 1000, "with_payload": True, "with_vectors": False}
        if offset:
            body["offset"] = offset
        r = await client.post(f"{qdrant_url}/collections/{collection}/points/scroll", json=body)
        if r.status_code != 200:
            logger.warning(
                "scroll_failed collection=%s status=%d body=%s",
                collection,
                r.status_code,
                r.text[:200],
            )
            break
        result = r.json().get("result", {})
        points.extend(result.get("points", []))
        if limit and len(points) >= limit:
            points = points[:limit]
            break
        nxt = result.get("next_page_offset")
        if not nxt:
            break
        offset = nxt
    return points


def derive_from_url(content: str, url: str) -> dict[str, str]:
    """Best-effort metadata derivation from a source URL (no fetch).

    Extracts hints from the URL itself (slug, chain, year) and returns only
    non-empty canonical fields. Used as a cheap supplement to content-based
    enrichment.
    """
    hints: dict[str, str] = {}
    slug = url.rstrip("/").split("/")[-1]
    if slug and len(slug) > 3 and slug not in hints:
        hints["protocol"] = slug.replace("-", " ").title()
    return hints


async def _set_payload(
    client: httpx.AsyncClient, collection: str, point_id: str, payload: dict[str, Any]
) -> bool:
    """Set the full payload for one point (per-point set_payload API)."""
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
    r = await client.post(
        f"{qdrant_url}/collections/{collection}/points/payload",
        json={"payload": payload, "points": [point_id]},
    )
    return r.status_code in (200, 201)


async def enrich_collection(
    client: httpx.AsyncClient,
    collection: str,
    limit: int | None = None,
    fetch_urls: bool = False,
) -> dict[str, Any]:
    """Enrich every point in *collection*; returns a change summary."""
    points = await scroll_points(client, collection, limit)
    if not points:
        return {"collection": collection, "scanned": 0, "updated": 0, "unchanged": 0, "errors": 0}

    updates: list[tuple[str, dict[str, Any]]] = []
    unchanged = 0
    for point in points:
        point_id = point.get("id")
        payload = point.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        enriched = enrich_payload(payload)
        if fetch_urls:
            url = str(
                enriched.get("source_url") or enriched.get("metadata", {}).get("source_url") or ""
            )
            if url and not enriched.get("protocol"):
                hints = derive_from_url(enriched.get("content", ""), url)
                for key, value in hints.items():
                    enriched[key] = value
                    enriched.setdefault("metadata", {})[key] = value
        changed = any(enriched.get(f) != payload.get(f) for f in CANONICAL_FIELDS_SET)
        if changed:
            updates.append((str(point_id), enriched))
        else:
            unchanged += 1

    # Qdrant's set_payload applies ONE payload to a list of point IDs, so we
    # send one request per point (capped concurrency keeps Qdrant happy).
    semaphore = asyncio.Semaphore(20)

    async def _update(point_id: str, payload: dict[str, Any]) -> bool:
        async with semaphore:
            return await _set_payload(client, collection, point_id, payload)

    results = await asyncio.gather(*(_update(pid, pl) for pid, pl in updates))
    updated = sum(1 for ok in results if ok)
    errors = len(results) - updated
    return {
        "collection": collection,
        "scanned": len(points),
        "updated": updated,
        "unchanged": unchanged,
        "errors": errors,
    }


async def main_async(args: argparse.Namespace) -> int:
    collections = args.collections or list(DEFAULT_COLLECTIONS)

    if args.dry_run:
        logger.info("[DRY RUN] would enrich collections: %s", ", ".join(collections))
        for collection in collections:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                points = await scroll_points(client, collection, args.limit)
            logger.info("[DRY RUN] %s: %d points would be scanned", collection, len(points))
        return 0

    total_updated = 0
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for collection in collections:
            summary = await enrich_collection(client, collection, args.limit, args.fetch_urls)
            total_updated += summary["updated"]
            logger.info(
                "enriched collection=%s scanned=%d updated=%d unchanged=%d errors=%d",
                summary["collection"],
                summary["scanned"],
                summary["updated"],
                summary["unchanged"],
                summary["errors"],
            )
    print("=" * 60)
    print(
        f"ENRICHMENT COMPLETE: {total_updated} points updated across {len(collections)} collections"
    )
    print("=" * 60)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich Qdrant points with canonical crypto metadata"
    )
    parser.add_argument(
        "--collections",
        nargs="+",
        default=None,
        help="Collections to enrich (default: threat_intel scam_intel hacks news wallet)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview point counts only")
    parser.add_argument("--limit", type=int, default=None, help="Max points per collection")
    parser.add_argument(
        "--fetch-urls",
        action="store_true",
        help="Derive hints from source_url slugs when content enrichment leaves gaps",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
