#!/usr/bin/env python3
"""Restore Qdrant collections from B2 permanence snapshots.

Lists available snapshots and imports a chosen day back into Qdrant
(merge upsert or full replace). Data on B2 is safe in cold storage:
this script is the disaster-recovery path for the nightly
``b2_permanence.py`` sync.

Both snapshot formats are supported:
    *.parquet        (default since 2026-08)  read via DuckDB
    *.jsonl.zst      (legacy)                 decompress to NDJSON

Objects are NOT client-side encrypted: B2 SSE-B2 (AES256) encrypts
everything at rest, so restore is simply download + decompress. Legacy
``*.jsonl.zst.enc`` objects (pre-2026-08-01) must be converted first
with ``migrate_to_parquet.py`` — client-side decryption was removed from
the restore path.

Usage:
    python scripts/b2_restore.py --list
    python scripts/b2_restore.py --list --date 2026-08-01
    python scripts/b2_restore.py --date 2026-08-01 --mode replace
    python scripts/b2_restore.py --collection threat_intel_v2 --mode merge
    python scripts/b2_restore.py --env-file /path/to/b2-permanence.env

Behavior:
    --list        print available snapshot dates (+ collections per date)
    --date        restore this day (default: latest per latest.json)
    --collection  restore only this collection (repeatable)
    --mode        merge (upsert, default) or replace (recreate collection)
    --dry-run     download + decode but do not write to Qdrant

Environment:
    B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY, B2_BUCKET_NAME,
    QDRANT_URL / QDRANT_PORT
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import qdrant_client
from b2sdk.v2 import B2Api
from qdrant_client.models import Distance, PointStruct, VectorParams

sys.path.insert(0, str(Path(__file__).resolve().parent))

import b2_common
from b2_common import (
    COMPRESSED_SUFFIX,
    ENCRYPTED_SUFFIX,
    PARQUET_SUFFIX,
    ensure_bucket,
    env_or,
    get_b2_api,
    list_object_keys,
    load_env_file,
    read_parquet_records,
)

logger = logging.getLogger("b2_restore")

MANIFEST_NAME = "manifest.json"
LATEST_NAME = "latest.json"
SNAPSHOT_PREFIX = "snapshots"
DEFAULT_BUCKET = "rmi-rag-permanence"
UPSERT_BATCH = 500


# ── B2 listing ─────────────────────────────────────────────────────────


def fetch_manifest(b2_api: B2Api, bucket_name: str, date_str: str) -> dict[str, Any]:
    """Download and parse manifest.json for a snapshot date.

    Returns {} if the date has no manifest (collections can still be
    restored by listing object keys).
    """
    key = f"{SNAPSHOT_PREFIX}/{date_str}/{MANIFEST_NAME}"
    with tempfile.TemporaryDirectory(prefix="b2-restore-manifest-") as tmp:
        local = Path(tmp) / MANIFEST_NAME
        try:
            b2_common.download_file(b2_api, bucket_name, key, local)
            data = json.loads(local.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("no manifest for %s: %s", date_str, exc)
            return {}


def list_snapshot_dates(b2_api: B2Api, bucket_name: str) -> list[str]:
    """Return snapshot dates (from latest.json pointer + folder listing)."""
    dates: list[str] = []
    keys = list_object_keys(b2_api, bucket_name, prefix=f"{SNAPSHOT_PREFIX}/")
    for k in keys:
        parts = k.split("/")
        if (
            len(parts) >= 3
            and parts[0] == SNAPSHOT_PREFIX
            and parts[1] not in (LATEST_NAME, MANIFEST_NAME)
            and parts[1] not in dates
        ):
            dates.append(parts[1])
    return sorted(dates)


def get_latest_date(b2_api: B2Api, bucket_name: str) -> str | None:
    """Resolve the most recent snapshot date from latest.json."""
    key = f"{SNAPSHOT_PREFIX}/{LATEST_NAME}"
    with tempfile.TemporaryDirectory(prefix="b2-restore-latest-") as tmp:
        local = Path(tmp) / LATEST_NAME
        try:
            b2_common.download_file(b2_api, bucket_name, key, local)
            data = json.loads(local.read_text(encoding="utf-8"))
            date_val = data.get("date")
            return str(date_val) if date_val else None
        except Exception as exc:
            logger.warning("latest.json unavailable: %s", exc)
    dates = list_snapshot_dates(b2_api, bucket_name)
    return dates[-1] if dates else None


# ── Decode ─────────────────────────────────────────────────────────────


def decode_snapshot(b2_api: B2Api, bucket_name: str, object_key: str, dest_dir: Path) -> Path:
    """Download + decompress a snapshot object; return readable path.

    Returns the parquet file directly for ``*.parquet`` objects (no
    decompression step — parquet carries its own zstd compression).
    Legacy ``*.jsonl.zst`` objects are decompressed to NDJSON.

    No decryption is performed: B2 SSE-B2 (AES256) encrypts objects at
    rest, and client-side AES-256-GCM was removed from the pipeline. A
    legacy ``*.jsonl.zst.enc`` object must be converted with
    ``migrate_to_parquet.py`` before it can be restored.
    """
    local_raw = dest_dir / object_key.replace("/", "_")
    b2_common.download_file(b2_api, bucket_name, object_key, local_raw)

    if object_key.endswith(PARQUET_SUFFIX):
        return local_raw

    if object_key.endswith(ENCRYPTED_SUFFIX):
        raise ValueError(
            f"{object_key} is a legacy client-side-encrypted (.enc) object. "
            "Client-side AES-256-GCM was removed 2026-08-01 (B2 SSE-B2 encrypts at "
            "rest). Convert it first with migrate_to_parquet.py, then restore the "
            "resulting parquet snapshot."
        )

    path = local_raw
    ndjson = dest_dir / (path.name + ".jsonl")
    b2_common.decompress_file(path, ndjson)
    return ndjson


def parse_point_id(raw: object) -> int | uuid.UUID:
    """Convert a serialized point ID back to int or UUID for Qdrant."""
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        # JSON round-trip: integers that were written as numbers stay ints;
        # UUIDs were stringified by json.dumps(default=str).
        if raw.isdigit():
            return int(raw)
        try:
            return uuid.UUID(raw)
        except ValueError as exc:
            raise ValueError(f"invalid point id in snapshot: {raw!r}") from exc
    raise ValueError(f"unsupported point id type: {type(raw).__name__}")


def record_to_point(rec: dict[str, Any]) -> PointStruct:
    """Convert one snapshot record (NDJSON dict or parquet row) to a PointStruct.

    Parquet rows carry ``payload`` (full JSON), ``embedding`` (flat dense
    vector) and ``vectors`` (named/sparse vector JSON). ``vector`` is a
    legacy NDJSON-only key.
    """
    payload_raw = rec.get("payload")
    payload: dict[str, Any] = {}
    if isinstance(payload_raw, str):
        try:
            parsed = json.loads(payload_raw)
            if isinstance(parsed, dict):
                payload = parsed
        except (TypeError, ValueError):
            payload = {}
    elif isinstance(payload_raw, dict):
        payload = payload_raw

    vector: Any = rec.get("vector")
    if vector is None:
        vectors_raw = rec.get("vectors")
        if vectors_raw:
            try:
                parsed = json.loads(str(vectors_raw))
                if isinstance(parsed, dict):
                    vector = parsed
            except (TypeError, ValueError):
                vector = None
        else:
            embedding = rec.get("embedding")
            if embedding is not None:
                vector = list(embedding)

    return PointStruct(
        id=parse_point_id(rec.get("id")),
        vector=vector or [],
        payload=payload or {},
    )


def parse_ndjson(path: Path) -> list[PointStruct]:
    """Load legacy NDJSON snapshot lines into Qdrant PointStruct objects."""
    points: list[PointStruct] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            points.append(record_to_point(rec))
    return points


def parse_parquet(path: Path) -> list[PointStruct]:
    """Load a parquet snapshot into Qdrant PointStruct objects via DuckDB."""
    records = read_parquet_records(path)
    return [record_to_point(rec) for rec in records]


def _prefer_format(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    """Prefer parquet manifest entries over legacy NDJSON when both exist."""
    return str(candidate.get("format")) == "parquet" and str(current.get("format")) != "parquet"


# ── Import ─────────────────────────────────────────────────────────────


def _parse_distance(distance: str | None) -> Distance:
    """Map a manifest distance string to a Qdrant Distance enum."""
    if distance:
        try:
            return Distance(distance.upper())
        except ValueError:
            logger.warning("unknown distance %r — defaulting to Cosine", distance)
    return Distance.COSINE


def _as_int(value: object) -> int | None:
    """Coerce a manifest value (JSON int/float/str) to int, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (float, str)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def ensure_collection(
    client: qdrant_client.QdrantClient,
    name: str,
    vector_size: int | None,
    distance: str | None,
) -> None:
    """Create the collection if missing (using manifest vector params)."""
    existing = [c.name for c in client.get_collections().collections]
    if name in existing:
        return
    size = vector_size or 2560  # canonical qwen3-embedding-4b dim (manifest may lack it)
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=size, distance=_parse_distance(distance)),
    )
    logger.info(
        "created_collection name=%s size=%d distance=%s", name, size, _parse_distance(distance)
    )


def import_points(
    client: qdrant_client.QdrantClient,
    name: str,
    points: list[PointStruct],
    mode: str,
    vector_size: int | None = None,
    distance: str | None = None,
) -> int:
    """Import points into Qdrant. mode=merge upserts; mode=replace
    recreates the collection first (dropping existing points)."""
    ensure_collection(client, name, vector_size, distance)
    if mode == "replace":
        logger.warning("replace_mode recreating_collection name=%s", name)
        client.recreate_collection(
            collection_name=name,
            vectors_config=VectorParams(
                size=vector_size or 2560,
                distance=_parse_distance(distance),
            ),
        )
    elif mode not in ("merge",):
        raise ValueError(f"unknown mode: {mode}")

    for i in range(0, len(points), UPSERT_BATCH):
        client.upsert(collection_name=name, points=points[i : i + UPSERT_BATCH])
    return len(points)


# ── CLI ────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Restore Qdrant collections from B2 permanence snapshots",
    )
    p.add_argument("--list", action="store_true", help="List available snapshots")
    p.add_argument("--date", default=None, help="Snapshot date YYYY-MM-DD (default: latest)")
    p.add_argument("--collection", action="append", default=[], help="Restrict to a collection")
    p.add_argument(
        "--mode",
        choices=["merge", "replace"],
        default="merge",
        help="merge=upsert (default), replace=recreate collection",
    )
    p.add_argument("--dry-run", action="store_true", help="Download/decode, no writes")
    p.add_argument("--qdrant-url", default=None, help="Qdrant URL override")
    p.add_argument("--qdrant-port", type=int, default=None, help="Qdrant port override")
    p.add_argument("--b2-bucket", default=None, help="B2 bucket override")
    p.add_argument("--env-file", default=None, help="Load KEY=value env file")
    p.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    return p


def resolve_qdrant_client(host: str | None, port: int | None) -> qdrant_client.QdrantClient:
    port_str = str(port) if port is not None else (os.getenv("QDRANT_PORT") or "6333")
    candidates = []
    if host:
        candidates.append(host)
    candidates.extend(
        [
            os.getenv("QDRANT_URL", "http://rmi-qdrant-v2:6333"),
            f"http://127.0.0.1:{port_str}",
            f"http://localhost:{port_str}",
        ]
    )
    seen: set[str] = set()
    errors: list[str] = []
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            client = qdrant_client.QdrantClient(url=url, timeout=30)
            _ = client.get_collections()
            logger.info("qdrant_connected url=%s", url)
            return client
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("Cannot reach Qdrant — tried " + "; ".join(errors))


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n //= 1024
    return f"{n:.1f}TB"


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logging.getLogger("b2sdk").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        load_env_file(args.env_file)
        b2_api = get_b2_api()
    except Exception as exc:
        logger.critical("b2_auth_failed: %s", exc)
        return 2

    bucket_name = args.b2_bucket or env_or("B2_BUCKET_NAME", DEFAULT_BUCKET)
    ensure_bucket(b2_api, bucket_name)

    if args.list:
        dates = list_snapshot_dates(b2_api, bucket_name)
        if not dates:
            print("No snapshots found in bucket:", bucket_name)
            return 0
        print(f"Snapshots in {bucket_name} ({len(dates)}):")
        for d in dates:
            manifest = fetch_manifest(b2_api, bucket_name, d)
            colls = [e.get("collection") for e in manifest.get("collections", [])]
            total = manifest.get("total_points", "?")
            line = f"  {d}  collections={len(colls) or '?'} points={total}"
            if colls:
                line += "  [" + ", ".join(str(c) for c in colls) + "]"
            print(line)
        return 0

    date_str = args.date or get_latest_date(b2_api, bucket_name)
    if not date_str:
        logger.critical("no snapshot date found — run with --list")
        return 1

    manifest = fetch_manifest(b2_api, bucket_name, date_str)
    entries = manifest.get("collections", [])
    keys = [
        k
        for k in list_object_keys(b2_api, bucket_name, prefix=f"{SNAPSHOT_PREFIX}/{date_str}/")
        if not k.endswith(MANIFEST_NAME)
    ]
    if not entries and not keys:
        logger.critical("no snapshot data for date %s", date_str)
        return 1

    logger.info(
        "restore_started date=%s mode=%s collections=%d dry_run=%s",
        date_str,
        args.mode,
        len(entries or keys),
        args.dry_run,
    )

    client = None if args.dry_run else resolve_qdrant_client(args.qdrant_url, args.qdrant_port)

    workdir = tempfile.TemporaryDirectory(prefix="b2-restore-")
    try:
        dest_dir = Path(workdir.name)
        restored_total = 0
        failures = 0

        # Build per-collection targets, preferring parquet when a migration
        # left both formats for the same collection.
        targets: list[dict[str, Any]] = []
        if entries:
            by_collection: dict[str, dict[str, Any]] = {}
            for e in entries:
                coll = str(e.get("collection", ""))
                prev = by_collection.get(coll)
                if prev is None or _prefer_format(e, prev):
                    by_collection[coll] = e
            targets = list(by_collection.values())
        else:
            for k in keys:
                stem = Path(k).stem
                coll = Path(k).stem if k.endswith(PARQUET_SUFFIX) else stem.split(".")[0]
                targets.append({"collection": coll, "object_key": k})

        for entry in targets:
            coll_name = str(entry.get("collection", ""))
            if args.collection and coll_name not in args.collection:
                continue
            fmt = str(entry.get("format") or "")
            object_key = str(entry.get("object_key") or "")
            if not object_key:
                suffix = PARQUET_SUFFIX if fmt == "parquet" else COMPRESSED_SUFFIX
                object_key = f"{SNAPSHOT_PREFIX}/{date_str}/{coll_name}{suffix}"
            logger.info("restoring collection=%s key=%s", coll_name, object_key)

            try:
                decoded = decode_snapshot(b2_api, bucket_name, object_key, dest_dir)
                if object_key.endswith(PARQUET_SUFFIX) or fmt == "parquet":
                    points = parse_parquet(decoded)
                else:
                    points = parse_ndjson(decoded)
                if args.dry_run:
                    logger.info("dry-run decoded %d points for %s", len(points), coll_name)
                    restored_total += len(points)
                    continue
                assert client is not None, "non-dry-run restore requires a Qdrant client"
                n = import_points(
                    client,
                    coll_name,
                    points,
                    args.mode,
                    vector_size=_as_int(entry.get("vector_size")),
                    distance=entry.get("distance"),
                )
                restored_total += n
                logger.info("restored collection=%s points=%d", coll_name, n)
            except Exception as exc:
                failures += 1
                logger.error("restore_failed collection=%s err=%s", coll_name, exc)

        logger.info(
            "restore_complete date=%s restored=%d failed=%d",
            date_str,
            restored_total,
            failures,
        )
        return 0 if failures == 0 else 1
    finally:
        workdir.cleanup()


if __name__ == "__main__":
    sys.exit(main())
