#!/usr/bin/env python3
"""Migrate dense-only Qdrant collections to dense + BM25-sparse hybrid search.

Qdrant cannot add sparse vectors to an existing collection (collection
config is immutable), so each collection is migrated into a ``{name}_v2``
collection that carries a named ``dense`` vector plus a ``bm25`` sparse
vector (fastembed ``Qdrant/bm25``). The original collection name can then
be pointed at ``_v2`` with a Qdrant alias.

Phases (safe by default — nothing is deleted without ``--swap``):
  1. Create ``{name}_v2`` with the hybrid config (named dense + bm25 sparse).
  2. Scroll points from the original collection and re-insert them into
     ``_v2`` with a freshly computed BM25 sparse vector.
  3. Verify ``_v2`` point counts against the source.
  4. ``--swap`` only: delete the old dense-only collection and alias the
     original name to ``_v2`` (strictly after verification passes).

Usage:
  python scripts/migrate_to_sparse.py --dry-run
  python scripts/migrate_to_sparse.py --collection threat_intel --limit 10
  python scripts/migrate_to_sparse.py --collection threat_intel
  python scripts/migrate_to_sparse.py
  python scripts/migrate_to_sparse.py --swap --collection threat_intel

Exit codes: 0 ok, 1 one or more collections failed, 2 BM25 embedder missing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Any

import httpx

from rugbrain import qdrant_store

logger = logging.getLogger("migrate_to_sparse")

DEFAULT_COLLECTIONS = [
    "threat_intel",
    "scam_intel",
    "hacks",
    "news",
    "scanner",
    "wallet",
    "research",
    "scams",
]
DENSE_NAME = "dense"
SPARSE_NAME = qdrant_store.SPARSE_VECTOR_NAME


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan only — no collections created, no data copied",
    )
    parser.add_argument(
        "--collection",
        help="Single collection to migrate (default: all known collections)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only migrate the first N points (testing)",
    )
    parser.add_argument(
        "--swap",
        action="store_true",
        help=(
            "After verification, delete the old dense-only collection and "
            "alias the original name to _v2 (destructive)"
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Points per scroll/upsert batch (default 100)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Qdrant base URL (default: QDRANT_URL env or http://127.0.0.1:6333)",
    )
    return parser.parse_args()


def _base_url(args: argparse.Namespace) -> str:
    url = args.url or os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
    return str(url).rstrip("/")


async def _get_collection_info(client: httpx.AsyncClient, name: str) -> dict[str, Any] | None:
    """Return the Qdrant collection info dict, or None when missing."""
    try:
        r = await client.get(f"/collections/{name}")
    except Exception as e:
        logger.warning("get_collection_failed name=%s err=%s", name, e)
        return None
    if r.status_code != 200:
        return None
    return r.json().get("result") or {}


def _dense_size(params: dict[str, Any]) -> int:
    """Extract the dense vector size from a collection params dict.

    Handles both legacy unnamed vectors (``{"size": N, ...}``) and named
    vectors (``{"dense": {"size": N, ...}}``).
    """
    vectors = params.get("vectors") or {}
    if isinstance(vectors, dict):
        if isinstance(vectors.get("size"), int):
            return int(vectors["size"])
        for cfg in vectors.values():
            if isinstance(cfg, dict) and isinstance(cfg.get("size"), int):
                return int(cfg["size"])
    return 1024


async def _create_v2(client: httpx.AsyncClient, v2_name: str, vector_size: int) -> bool:
    """Create a ``_v2`` collection with named dense + BM25 sparse config."""
    payload: dict[str, Any] = {
        "vectors": {DENSE_NAME: {"size": vector_size, "distance": "Cosine"}},
        "sparse_vectors": {SPARSE_NAME: {}},
    }
    try:
        r = await client.put(f"/collections/{v2_name}", json=payload)
    except Exception as e:
        logger.warning("create_v2_failed name=%s err=%s", v2_name, e)
        return False
    return r.status_code in (200, 201, 202)


async def _copy_points(
    client: httpx.AsyncClient,
    src: str,
    dst: str,
    args: argparse.Namespace,
) -> tuple[int, int, int]:
    """Copy points from *src* to *dst*, adding BM25 sparse vectors.

    Returns ``(migrated, skipped, missing_sparse)``. Points without a
    dense vector are skipped; points with empty/embedder-missing content
    are written with only the dense vector and counted in missing_sparse.
    """
    migrated = 0
    skipped = 0
    missing_sparse = 0
    offset: Any = None

    while args.limit is None or migrated < args.limit:
        body: dict[str, Any] = {
            "limit": min(args.batch_size, 1000),
            "with_payload": True,
            "with_vectors": True,
        }
        if offset is not None:
            body["offset"] = offset
        try:
            r = await client.post(f"/collections/{src}/points/scroll", json=body)
        except Exception as e:
            logger.warning("scroll_failed src=%s err=%s", src, e)
            break
        if r.status_code != 200:
            logger.error("scroll_error src=%s status=%d body=%s", src, r.status_code, r.text[:200])
            break
        result = r.json().get("result", {})
        points = result.get("points", [])
        if not points:
            break

        batch: list[dict[str, Any]] = []
        for point in points:
            if args.limit is not None and migrated >= args.limit:
                break
            raw_vector = point.get("vector")
            if isinstance(raw_vector, dict):
                dense = raw_vector.get(DENSE_NAME) or raw_vector.get("vector")
            else:
                dense = raw_vector
            if not dense:
                skipped += 1
                continue
            payload = point.get("payload") or {}
            sparse = await qdrant_store._embed_sparse(payload.get("content", ""))
            vector: dict[str, Any] = {DENSE_NAME: dense}
            if sparse is not None:
                vector[SPARSE_NAME] = {"indices": sparse[0], "values": sparse[1]}
            else:
                missing_sparse += 1
            batch.append({"id": point.get("id"), "vector": vector, "payload": payload})
            migrated += 1

        if batch:
            try:
                r = await client.put(f"/collections/{dst}/points", json={"points": batch})
            except Exception as e:
                logger.warning("upsert_failed dst=%s err=%s", dst, e)
                return migrated, skipped, missing_sparse
            if r.status_code not in (200, 201, 202):
                logger.error(
                    "upsert_error dst=%s status=%d body=%s",
                    dst,
                    r.status_code,
                    r.text[:200],
                )
                return migrated, skipped, missing_sparse

        next_offset = result.get("next_page_offset")
        if next_offset is None:
            break
        offset = next_offset

    return migrated, skipped, missing_sparse


async def _swap_alias(client: httpx.AsyncClient, name: str, v2: str) -> str:
    """Point *name* at the hybrid ``_v2`` collection via a Qdrant alias.

    Qdrant rejects an alias whose name matches an existing collection, so
    the old dense-only collection is deleted first. Idempotent: if *name*
    already resolves to a hybrid collection, this is a no-op.
    """
    info = await _get_collection_info(client, name)
    if info:
        params = info.get("config", {}).get("params", {}) or {}
        if params.get("sparse_vectors"):
            logger.info("swap_skip name=%s already hybrid", name)
            return "swapped"
        try:
            r = await client.delete(f"/collections/{name}")
        except Exception as e:
            logger.warning("delete_old_failed name=%s err=%s", name, e)
            return "swap-failed"
        if r.status_code not in (200, 202):
            logger.error(
                "delete_old_error name=%s status=%d body=%s",
                name,
                r.status_code,
                r.text[:200],
            )
            return "swap-failed"
        logger.info("swap_deleted_old name=%s", name)

    body = {"actions": [{"create_alias": {"collection_name": v2, "alias_name": name}}]}
    try:
        r = await client.post("/collections/aliases", json=body)
    except Exception as e:
        logger.warning("create_alias_failed name=%s err=%s", name, e)
        return "swap-failed"
    if r.status_code not in (200, 202):
        logger.error(
            "create_alias_error name=%s status=%d body=%s",
            name,
            r.status_code,
            r.text[:200],
        )
        return "swap-failed"

    info = await _get_collection_info(client, name)
    params = (info or {}).get("config", {}).get("params", {}) or {}
    if not params.get("sparse_vectors"):
        logger.error("swap_verify_failed name=%s", name)
        return "swap-failed"
    logger.info(
        "swap_ok name=%s alias=%s points=%s",
        name,
        v2,
        (info or {}).get("points_count"),
    )
    return "swapped"


async def _migrate_collection(
    client: httpx.AsyncClient, name: str, args: argparse.Namespace
) -> str:
    info = await _get_collection_info(client, name)
    if info is None:
        logger.warning("skip name=%s reason=collection does not exist", name)
        return "missing"
    params = info.get("config", {}).get("params", {}) or {}
    sparse_config = params.get("sparse_vectors") or {}
    if sparse_config:
        logger.info("skip name=%s reason=already hybrid sparse=%s", name, list(sparse_config))
        return "already-hybrid"

    vector_size = _dense_size(params)
    points_count = int(info.get("points_count") or 0)
    v2 = f"{name}_v2"

    # If a hybrid _v2 collection already exists from an earlier migrate run,
    # skip create+copy and go straight to the alias swap (idempotent). The
    # original dense-only collection must still be swapped, so we do NOT
    # short-circuit on "already-hybrid" here — only when _v2 is complete.
    v2_info = await _get_collection_info(client, v2)
    v2_count = int((v2_info or {}).get("points_count") or 0)
    v2_params = (v2_info or {}).get("config", {}).get("params", {}) or {}
    v2_hybrid = bool(v2_params.get("sparse_vectors"))

    if args.dry_run:
        logger.info(
            "plan name=%s v2=%s dense_size=%d sparse=%s points=%d v2_points=%d swap=%s",
            name,
            v2,
            vector_size,
            SPARSE_NAME,
            points_count,
            v2_count,
            args.swap,
        )
        return "planned"

    if v2_info is not None and v2_hybrid and v2_count == points_count and args.limit is None:
        logger.info(
            "skip_copy name=%s v2=%s already migrated points=%d",
            name,
            v2,
            v2_count,
        )
        if args.swap:
            return await _swap_alias(client, name, v2)
        logger.info(
            "note name=%s alias swap not run — verify %s then rerun with --swap",
            name,
            v2,
        )
        return "migrated"

    # _v2 may already exist (e.g. a partial run or a seeded-after-migration
    # collection like hacks). Qdrant returns 409 on re-PUT of an existing
    # collection, so only create when missing; otherwise copy into it
    # (upserts are idempotent) to backfill the missing points.
    if v2_info is None:
        if not await _create_v2(client, v2, vector_size):
            logger.error("fail name=%s stage=create_v2", name)
            return "create-failed"
    elif not v2_hybrid:
        logger.error(
            "fail name=%s stage=create_v2 v2 exists but is not hybrid sparse=%s",
            name,
            v2_params.get("sparse_vectors"),
        )
        return "create-failed"
    else:
        logger.info(
            "reuse name=%s v2=%s exists hybrid points=%d — backfilling",
            name,
            v2,
            v2_count,
        )

    migrated, skipped, missing_sparse = await _copy_points(client, name, v2, args)

    v2_info = await _get_collection_info(client, v2)
    v2_count = int((v2_info or {}).get("points_count") or 0)
    if migrated > 0 and v2_count != migrated:
        logger.error(
            "fail name=%s stage=verify migrated=%d v2_count=%d",
            name,
            migrated,
            v2_count,
        )
        return "verify-failed"
    if points_count and args.limit is None and migrated != points_count:
        logger.error(
            "fail name=%s stage=verify migrated=%d source=%d",
            name,
            migrated,
            points_count,
        )
        return "verify-failed"
    logger.info(
        "ok name=%s migrated=%d skipped=%d missing_sparse=%d v2_count=%d",
        name,
        migrated,
        skipped,
        missing_sparse,
        v2_count,
    )

    if args.swap:
        return await _swap_alias(client, name, v2)
    logger.info(
        "note name=%s alias swap not run — verify %s then rerun with --swap",
        name,
        v2,
    )
    return "migrated"


async def _main() -> int:
    args = build_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not args.dry_run and qdrant_store._get_bm25_embedder() is None:
        logger.error(
            "BM25 embedder unavailable — install fastembed: pip install 'fastembed>=0.8.0'"
        )
        return 2

    names = [args.collection] if args.collection else DEFAULT_COLLECTIONS
    results: dict[str, str] = {}
    async with httpx.AsyncClient(base_url=_base_url(args), timeout=60.0) as client:
        for name in names:
            results[name] = await _migrate_collection(client, name, args)

    logger.info("summary: %s", results)
    failed = [
        n for n, s in results.items() if s in ("create-failed", "verify-failed", "swap-failed")
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
