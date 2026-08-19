#!/usr/bin/env python3
"""Nightly B2 permanence sync — Qdrant snapshots to Backblaze B2.

Exports every Qdrant collection as a columnar parquet file (default)
or compressed NDJSON (legacy fallback) and uploads it to B2 under
``snapshots/YYYY-MM-DD/``:

    b2://{B2_BUCKET_NAME}/snapshots/{date}/{collection}.parquet       (default)
    b2://{B2_BUCKET_NAME}/snapshots/{date}/{collection}.jsonl.zst     (legacy)

Snapshots are written with zstd level 19 and uploaded WITHOUT
client-side encryption: B2 server-side encryption (SSE-B2, AES256)
encrypts every object at rest for free. DuckDB can query parquet
directly from B2 over the S3-compatible API (no full download). Access
control is the private bucket + a scoped application key.

A ``manifest.json`` describing every collection (format, point count,
file size, sha256, timestamps, vector metadata) is uploaded alongside
the data and a ``snapshots/latest.json`` pointer records the most
recent date.

Data lifecycle this serves:
    Hot  (Qdrant): last 90 days, actively searched
    Cold (B2):     everything, forever, nightly snapshots
    Pruning:       documents older than 90 days are moved to B2 only
                   (kept in Qdrant if high severity) — see
                   ``src/rugbrain/qdrant_store.py::prune_old_documents``

Usage:
    python scripts/b2_permanence.py --dry-run
    python scripts/b2_permanence.py --collection threat_intel_v2 --limit 10
    python scripts/b2_permanence.py --format ndjson --limit 50
    python scripts/b2_permanence.py --env-file /path/to/b2-permanence.env
    python scripts/b2_permanence.py --prune              # retention dry-run
    python scripts/b2_permanence.py --prune --prune-live # actually prune

Environment:
    B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY   (required)
    B2_BUCKET_NAME                              (default rmi-rag-permanence)
    B2_ENCRYPTION_KEY                           DEPRECATED — SSE-B2 encrypts
                                                at rest; kept only for
                                                reading/migrating legacy .enc
    QDRANT_URL / QDRANT_PORT                    Qdrant connection

See scripts/b2_common.py for the file format. Restore:
scripts/b2_restore.py. Convert legacy snapshots: scripts/migrate_to_parquet.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import qdrant_client
import qdrant_client.http.exceptions
from b2sdk.v2 import B2Api

sys.path.insert(0, str(Path(__file__).resolve().parent))

import b2_common
from b2_common import (
    COMPRESSED_SUFFIX,
    NDJSON_SUFFIX,
    PARQUET_SUFFIX,
    compress_file,
    ensure_bucket,
    env_or,
    get_b2_api,
    load_env_file,
    sha256_file,
    stream_parquet,
    upload_file,
)

logger = logging.getLogger("b2_permanence")

# Qdrant fallback chain (mirrors src/rugbrain/qdrant_store.py)
DEFAULT_QDRANT_HOST = os.getenv("QDRANT_URL", "http://rmi-qdrant:6333")
DEFAULT_QDRANT_PORT = os.getenv("QDRANT_PORT", "6333")

MANIFEST_NAME = "manifest.json"
LATEST_NAME = "latest.json"
SNAPSHOT_PREFIX = "snapshots"
SCROLL_BATCH = 1000
MANIFEST_SCHEMA_VERSION = 2


# ── Qdrant helpers ────────────────────────────────────────────────────


def resolve_qdrant_client(host: str | None, port: int | None) -> qdrant_client.QdrantClient:
    """Build a QdrantClient, trying the fallback chain until one answers.

    Chain: explicit --qdrant-url → QDRANT_URL → 127.0.0.1:port →
    localhost:port. Returns a client for the first reachable URL.
    """
    port_str = str(port or DEFAULT_QDRANT_PORT)
    candidates: list[str] = []
    if host:
        candidates.append(host)
    candidates.extend(
        [
            DEFAULT_QDRANT_HOST,
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
            logger.debug("qdrant_probe_failed url=%s err=%s", url, exc)

    raise RuntimeError("Cannot reach Qdrant — tried " + "; ".join(errors))


def list_collections(client: qdrant_client.QdrantClient) -> list[str]:
    """Return collection names sorted alphabetically."""
    return sorted(c.name for c in client.get_collections().collections)


def collection_info(client: qdrant_client.QdrantClient, name: str) -> dict[str, Any]:
    """Return vector size/distance metadata for the manifest."""
    try:
        info = client.get_collection(collection_name=name)
        vectors = info.config.params.vectors
        size: object = getattr(vectors, "size", None)
        distance: object = getattr(vectors, "distance", None)
        return {"vector_size": size, "distance": str(distance) if distance else None}
    except Exception as exc:
        logger.warning("collection_info_failed collection=%s err=%s", name, exc)
        return {"vector_size": None, "distance": None}


def export_collection_parquet(
    client: qdrant_client.QdrantClient,
    name: str,
    dest: Path,
    limit: int | None = None,
    with_vectors: bool = True,
) -> int:
    """Scroll all points in *name* into a columnar parquet file at *dest*.

    Points are converted to Arrow tables per scroll batch and streamed
    through a ParquetWriter (bounded memory), then compressed with zstd
    level 19 inside the parquet file itself. Returns the point count.
    """
    count_cell: list[int] = [0]

    def batches() -> Iterable[list[dict[str, object]]]:
        offset: Any = None
        remaining = limit
        while True:
            batch = min(remaining if remaining is not None else SCROLL_BATCH, SCROLL_BATCH)
            points, next_offset = client.scroll(
                collection_name=name,
                limit=batch,
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            )
            if points:
                rows = [b2_common.point_to_row(p, with_vectors=with_vectors) for p in points]
                count_cell[0] += len(rows)
                yield rows
            if next_offset is None:
                break
            offset = next_offset
            if len(points) == 0:
                break
            if remaining is not None:
                remaining -= len(points)
                if remaining <= 0:
                    break

    stream_parquet(dest, batches())
    return count_cell[0]


def export_collection_ndjson(
    client: qdrant_client.QdrantClient,
    name: str,
    dest: Path,
    limit: int | None = None,
    with_vectors: bool = True,
) -> int:
    """Scroll all points in *name* into NDJSON lines at *dest*.

    Each line is one JSON object: {"id", "vector", "payload"}.
    Returns the number of points written.
    """
    count = 0
    offset: Any = None
    remaining = limit

    with open(dest, "w", encoding="utf-8") as f:
        while True:
            batch = min(remaining if remaining is not None else SCROLL_BATCH, SCROLL_BATCH)
            points, next_offset = client.scroll(
                collection_name=name,
                limit=batch,
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            )
            for p in points:
                # Preserve the original ID type: int stays int, UUIDs are
                # serialized to string by default=str. Restore converts back.
                record: dict[str, object] = {"id": p.id, "payload": p.payload or {}}
                if with_vectors:
                    record["vector"] = p.vector
                f.write(json.dumps(record, default=str) + "\n")
                count += 1
                if remaining is not None:
                    remaining -= 1
                    if remaining <= 0:
                        return count
            if next_offset is None:
                break
            offset = next_offset
            if len(points) == 0:
                break
    return count


# ── Snapshot pipeline ─────────────────────────────────────────────────


def snapshot_collection(
    client: qdrant_client.QdrantClient,
    b2_api: B2Api,
    bucket_name: str,
    date_str: str,
    name: str,
    workdir: Path,
    limit: int | None,
    with_vectors: bool,
    fmt: str = "parquet",
) -> dict[str, Any]:
    """Export, compress (parquet/NDJSON) and upload one collection.

    ``fmt`` is ``parquet`` (default — columnar, DuckDB-queryable from B2)
    or ``ndjson`` (legacy — zstd). No client-side encryption is applied:
    B2 SSE-B2 (AES256) encrypts every object at rest.

    Returns the manifest entry for this collection.
    """
    base = workdir / name
    base.mkdir(parents=True, exist_ok=True)

    started = datetime.now(UTC)

    if fmt == "parquet":
        parquet_path = base / f"{name}{PARQUET_SUFFIX}"
        point_count = export_collection_parquet(
            client, name, parquet_path, limit=limit, with_vectors=with_vectors
        )
        upload_path = parquet_path
        suffix = PARQUET_SUFFIX
    else:
        ndjson_path = base / f"{name}{NDJSON_SUFFIX}"
        zst_path = base / f"{name}{NDJSON_SUFFIX}{COMPRESSED_SUFFIX}"
        point_count = export_collection_ndjson(
            client, name, ndjson_path, limit=limit, with_vectors=with_vectors
        )
        compress_file(ndjson_path, zst_path, level=19)
        upload_path = zst_path
        suffix = f"{NDJSON_SUFFIX}{COMPRESSED_SUFFIX}"

    object_key = f"{SNAPSHOT_PREFIX}/{date_str}/{name}{suffix}"
    upload_file(b2_api, bucket_name, upload_path, object_key)

    finished = datetime.now(UTC)
    entry: dict[str, Any] = {
        "collection": name,
        "format": fmt,
        "object_key": object_key,
        "point_count": point_count,
        "bytes_raw": upload_path.stat().st_size if fmt == "parquet" else ndjson_path.stat().st_size,  # type: ignore[possibly-undefined]
        "bytes_stored": upload_path.stat().st_size,
        "compression": "zstd-19" if fmt == "ndjson" else "parquet-zstd-19",
        "encrypted": False,  # SSE-B2 encrypts at rest; no client-side encryption
        "sha256": sha256_file(upload_path),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
    }
    if with_vectors:
        entry["vectors_included"] = True
    logger.info(
        "snapshot_ok collection=%s format=%s points=%d stored=%s key=%s",
        name,
        fmt,
        point_count,
        _fmt_size(upload_path.stat().st_size),
        object_key,
    )
    return entry


def upload_manifest(
    b2_api: B2Api, bucket_name: str, date_str: str, entries: list[dict[str, Any]]
) -> None:
    """Upload manifest.json for the day plus the latest.json pointer."""
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "date": date_str,
        "encryption": {
            "algorithm": "SSE-B2",
            "key_env": None,
            "enabled": True,
            "note": "B2 server-side encryption (AES256, SSE-B2) encrypts all objects "
            "at rest for free. No client-side encryption is applied; client-side "
            "AES-256-GCM was removed 2026-08-01.",
        },
        "formats": sorted({str(e.get("format", "ndjson")) for e in entries}),
        "collections": entries,
        "total_points": sum(int(e.get("point_count", 0)) for e in entries),
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(manifest, tmp, indent=2)
        tmp_path = Path(tmp.name)
    try:
        upload_file(b2_api, bucket_name, tmp_path, f"{SNAPSHOT_PREFIX}/{date_str}/{MANIFEST_NAME}")
        # latest.json pointer (uploaded last so restore can trust it)
        latest = {"date": date_str, "manifest": f"{SNAPSHOT_PREFIX}/{date_str}/{MANIFEST_NAME}"}
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(latest, f, indent=2)
        upload_file(b2_api, bucket_name, tmp_path, f"{SNAPSHOT_PREFIX}/{LATEST_NAME}")
    finally:
        tmp_path.unlink(missing_ok=True)
    logger.info("manifest_uploaded date=%s collections=%d", date_str, len(entries))


# ── Retention / prune ──────────────────────────────────────────────
#
# Policy (BACKUP-POLICY.md, RAG Permanence):
#   keep 30 daily / 4 weekly / 12 monthly snapshots.
#   A snapshot is a dated directory: snapshots/YYYY-MM-DD/{...}.
#   Daily   = newest 30 dated snapshot dirs
#   Weekly  = newest snapshot dir in each of the last 4 ISO weeks
#   Monthly = newest snapshot dir in each of the last 12 calendar months
# Deletion is DATED-DIR-GRANULAR and scoped to the snapshots/ prefix.
# latest.json / manifest.json pointers are never deleted.

SNAPSHOT_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
# Retention counts — sane equivalent of the documented 30/4/12
RETAIN_DAILY = 30
RETAIN_WEEKLY = 4
RETAIN_MONTHLY = 12
# Safety floor: never prune below this many retained snapshots.
# Prevents a misconfig from deleting everything (e.g. empty bucket edge).
MIN_RETAIN_FLOOR = 1


def parse_snapshot_date(key: str) -> str | None:
    """Extract the ``YYYY-MM-DD`` snapshot dir from an object key.

    Only keys under ``snapshots/YYYY-MM-DD/`` (with a REAL calendar date)
    qualify; everything else (latest.json, manifest.json at the prefix
    root, stray objects, malformed dates) returns None so the prune pass
    can never touch it.
    """
    parts = key.split("/")
    if len(parts) >= 2 and parts[0] == SNAPSHOT_PREFIX:
        candidate = parts[1]
        if SNAPSHOT_DATE_RE.match(candidate):
            # Reject syntactically-shaped but invalid dates (e.g. 2026-13-99)
            try:
                datetime.strptime(candidate, "%Y-%m-%d")
            except ValueError:
                return None
            return candidate
    return None


def snapshot_dates_on_remote(b2_api: B2Api, bucket_name: str) -> list[str]:
    """Return the sorted list of dated snapshot dirs present on B2."""
    bucket = ensure_bucket(b2_api, bucket_name)
    dates: set[str] = set()
    # b2sdk ls with recursive=True returns (version_info, folder_name) tuples.
    for version, _folder in bucket.ls(f"{SNAPSHOT_PREFIX}/", recursive=True):
        date = parse_snapshot_date(version.file_name)
        if date:
            dates.add(date)
    return sorted(dates)


def retention_keep_set(
    dates: list[str],
    keep_daily: int = RETAIN_DAILY,
    keep_weekly: int = RETAIN_WEEKLY,
    keep_monthly: int = RETAIN_MONTHLY,
) -> set[str]:
    """Compute the set of snapshot dates to KEEP under the 30/4/12 policy.

    ``dates`` must be sorted ascending. Returns the union of:
      - the newest ``keep_daily`` dates (daily cadence)
      - the newest snapshot date within each of the last ``keep_weekly``
        ISO weeks (weekly cadence)
      - the newest snapshot date within each of the last ``keep_monthly``
        calendar months (monthly cadence)
    """
    keep: set[str] = set()
    if not dates:
        return keep

    # Daily — newest N dates
    keep.update(dates[-keep_daily:])

    # Weekly — newest date per ISO (year, week)
    by_week: dict[tuple[int, int], str] = {}
    for d in dates:
        dt = datetime.strptime(d, "%Y-%m-%d")
        iso = dt.isocalendar()
        by_week[(iso[0], iso[1])] = d  # ascending iter => last wins = newest
    for _week, date in list(sorted(by_week.items()))[-keep_weekly:]:
        keep.add(date)

    # Monthly — newest date per (year, month)
    by_month: dict[tuple[int, int], str] = {}
    for d in dates:
        dt = datetime.strptime(d, "%Y-%m-%d")
        by_month[(dt.year, dt.month)] = d  # ascending iter => last wins = newest
    for _month, date in list(sorted(by_month.items()))[-keep_monthly:]:
        keep.add(date)

    return keep


def prune_snapshots(
    b2_api: B2Api,
    bucket_name: str,
    dry_run: bool = True,
    keep_daily: int = RETAIN_DAILY,
    keep_weekly: int = RETAIN_WEEKLY,
    keep_monthly: int = RETAIN_MONTHLY,
) -> dict[str, Any]:
    """Enforce snapshot retention on the remote (30 daily / 4 weekly / 12 monthly).

    Deletes whole dated snapshot dirs that are NOT in the keep set. Deletion
    is scoped strictly to ``snapshots/YYYY-MM-DD/`` — pointers and stray
    objects are never touched.

    SAFETY GUARDS:
      * ``dry_run=True`` (default) only logs what would be deleted.
      * Refuses the permanent/object-lock bucket (``rugmunch-permanent``).
      * Refuses to prune when the candidate set would leave fewer than
        ``MIN_RETAIN_FLOOR`` snapshots (protects against wiping everything).
      * Never deletes the newest snapshot date.
      * No-op when there is nothing to prune.

    Returns a summary dict.
    """
    result: dict[str, Any] = {"bucket": bucket_name, "dry_run": dry_run}

    # SAFETY: never prune the object-lock/permanent bucket
    if "permanent" in bucket_name.lower() or bucket_name in {
        "rugmunch-permanent",
        "rmi-backup-encrypted",
    }:
        logger.error(
            "prune_refused bucket=%s permanent/object-lock bucket — refusing to prune",
            bucket_name,
        )
        result["refused"] = "permanent-bucket"
        return result

    dates = snapshot_dates_on_remote(b2_api, bucket_name)
    result["snapshot_dates"] = len(dates)
    if not dates:
        logger.info("prune: no dated snapshots found on %s — nothing to do", bucket_name)
        return result

    keep = retention_keep_set(dates, keep_daily, keep_weekly, keep_monthly)

    # SAFETY: floor guard — if the raw retention policy would retain
    # NOTHING (empty keep set), refuse before any deletion. This is the
    # "would delete everything" protection: a misconfigured/zeroed policy
    # must never translate into wiping the remote.
    if len(keep) == 0:
        logger.error(
            "prune_refused: retention policy retained 0 of %d snapshots on %s — aborting",
            len(dates),
            bucket_name,
        )
        result["refused"] = "floor-guard"
        return result

    # SAFETY: never delete the newest snapshot (belt & braces — it is in keep)
    keep.add(dates[-1])

    to_delete = [d for d in dates if d not in keep]
    result["keep"] = len(keep)
    result["would_delete"] = len(to_delete)

    if not to_delete:
        logger.info(
            "prune: nothing to prune (snapshots=%d kept=%d)",
            len(dates),
            len(keep),
        )
        return result

    # SAFETY: floor guard — never leave fewer than MIN_RETAIN_FLOOR snapshots.
    # Defensive: len(to_delete) >= len(dates) is impossible after the
    # newest-date keep above, but is checked anyway as belt-and-braces.
    if len(to_delete) >= len(dates):
        logger.error(
            "prune_refused: would delete %d/%d snapshots on %s — aborting",
            len(to_delete),
            len(dates),
            bucket_name,
        )
        result["refused"] = "floor-guard"
        return result
    retained_after = len(dates) - len(to_delete)
    if retained_after < MIN_RETAIN_FLOOR:
        logger.error(
            "prune_refused: would leave %d snapshots (floor %d) on %s — aborting",
            retained_after,
            MIN_RETAIN_FLOOR,
            bucket_name,
        )
        result["refused"] = "floor-guard"
        return result

    logger.info(
        "prune: snapshots=%d keep=%d delete=%d (dry_run=%s)",
        len(dates),
        len(keep),
        len(to_delete),
        dry_run,
    )

    bucket = ensure_bucket(b2_api, bucket_name)
    deleted_dirs: list[str] = []
    deleted_objects = 0
    for date in to_delete:
        prefix = f"{SNAPSHOT_PREFIX}/{date}/"
        version_infos = [
            version
            for version, _folder in bucket.ls(prefix, recursive=True)
            if parse_snapshot_date(version.file_name)
        ]
        if dry_run:
            logger.info(
                "prune_dry_run: would delete snapshots/%s/ (%d objects)",
                date,
                len(version_infos),
            )
            continue
        # Live delete — file_id + file_name required by b2sdk v2
        for version in version_infos:
            bucket.delete_file_version(version.id_, version.file_name)
            deleted_objects += 1
        deleted_dirs.append(date)
        logger.info("prune_deleted date=%s objects=%d", date, len(version_infos))

    result["deleted_dirs"] = deleted_dirs
    result["deleted_objects"] = deleted_objects
    return result


# ── CLI ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Nightly Qdrant → B2 permanence sync (parquet by default, "
        "compressed NDJSON fallback). B2 SSE-B2 encrypts objects at rest.",
    )
    p.add_argument("--dry-run", action="store_true", help="Do everything except upload")
    p.add_argument(
        "--format",
        choices=["parquet", "ndjson"],
        default="parquet",
        help="Snapshot format: parquet (default, DuckDB-queryable) or ndjson (legacy)",
    )
    p.add_argument(
        "--collection",
        action="append",
        default=[],
        help="Restrict to a collection (repeatable; default: all)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max points to export per collection (testing)",
    )
    p.add_argument(
        "--no-vectors", action="store_true", help="Skip vectors in export (payload only)"
    )
    p.add_argument("--qdrant-url", default=None, help="Qdrant URL override")
    p.add_argument("--qdrant-port", type=int, default=None, help="Qdrant port override")
    p.add_argument("--b2-bucket", default=None, help="B2 bucket override")
    p.add_argument(
        "--env-file",
        default=None,
        help="Load KEY=value env file (e.g. /path/to/b2-permanence.env)",
    )
    p.add_argument(
        "--prune",
        action="store_true",
        help="Enable remote retention pruning (keep 30 daily / 4 weekly / 12 "
        "monthly). Dry-run by default — pass --prune-live (and NOT --dry-run) "
        "to actually delete.",
    )
    p.add_argument(
        "--prune-live",
        action="store_true",
        help="With --prune: actually delete expired snapshot dirs instead of "
        "dry-run. Ignored if --dry-run is set. Requires --prune.",
    )
    p.add_argument(
        "--keep-daily",
        type=int,
        default=RETAIN_DAILY,
        help=f"Daily snapshots to keep (default {RETAIN_DAILY})",
    )
    p.add_argument(
        "--keep-weekly",
        type=int,
        default=RETAIN_WEEKLY,
        help=f"Weekly snapshots to keep (default {RETAIN_WEEKLY})",
    )
    p.add_argument(
        "--keep-monthly",
        type=int,
        default=RETAIN_MONTHLY,
        help=f"Monthly snapshots to keep (default {RETAIN_MONTHLY})",
    )
    p.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    return p


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
        client = resolve_qdrant_client(args.qdrant_url, args.qdrant_port)
    except Exception as exc:
        logger.critical("startup_failed: %s", exc)
        return 2

    bucket_name = args.b2_bucket or env_or("B2_BUCKET_NAME", b2_common.DEFAULT_BUCKET)
    fmt = args.format

    # B2_ENCRYPTION_KEY is deprecated: SSE-B2 (AES256) encrypts objects at
    # rest, so client-side encryption is no longer applied. Warn (not fail)
    # so operators can drop the key from the env file once legacy .enc
    # snapshots have been migrated.
    if env_or("B2_ENCRYPTION_KEY"):
        logger.warning(
            "Using B2 SSE-B2 — client-side encryption key no longer needed. "
            "B2_ENCRYPTION_KEY is deprecated and is ignored by the upload path "
            "(still used only for reading/migrating legacy .enc objects)."
        )

    with_vectors = not args.no_vectors

    try:
        collections = list_collections(client)
    except Exception as exc:
        logger.critical("list_collections_failed: %s", exc)
        return 1

    if args.collection:
        requested = set(args.collection)
        missing = requested - set(collections)
        if missing:
            logger.critical("unknown collection(s): %s (have: %s)", sorted(missing), collections)
            return 1
        collections = [c for c in collections if c in requested]

    if not collections:
        logger.warning("no collections to snapshot")
        return 0

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    logger.info("=== B2 permanence sync started ===")
    logger.info(
        "bucket=%s date=%s collections=%d format=%s vectors=%s",
        bucket_name,
        date_str,
        len(collections),
        fmt,
        with_vectors,
    )

    if args.dry_run:
        for c in collections:
            info = collection_info(client, c)
            logger.info(
                "dry-run would snapshot collection=%s format=%s points=<all> vector_size=%s",
                c,
                fmt,
                info.get("vector_size"),
            )
        # If retention pruning was requested, show what WOULD be pruned.
        # Best-effort: B2 creds may be absent in pure dry-run; never delete.
        if args.prune:
            try:
                b2_api = get_b2_api()
                prune_snapshots(
                    b2_api,
                    bucket_name,
                    dry_run=True,
                    keep_daily=args.keep_daily,
                    keep_weekly=args.keep_weekly,
                    keep_monthly=args.keep_monthly,
                )
            except Exception as exc:
                logger.warning("prune_dry_run_skipped: %s", exc)
        logger.info("=== dry-run complete (no uploads, no deletes) ===")
        return 0

    # Authorize B2 + ensure bucket (after dry-run so no creds needed to list)
    try:
        b2_api = get_b2_api()
        ensure_bucket(b2_api, bucket_name)
    except Exception as exc:
        logger.critical("b2_auth_failed: %s", exc)
        return 2

    entries: list[dict[str, Any]] = []
    failures = 0
    with tempfile.TemporaryDirectory(prefix="b2-permanence-") as workdir_str:
        workdir = Path(workdir_str)
        for idx, name in enumerate(collections, 1):
            logger.info("[%d/%d] collection=%s", idx, len(collections), name)
            try:
                entries.append(
                    snapshot_collection(
                        client,
                        b2_api,
                        bucket_name,
                        date_str,
                        name,
                        workdir,
                        args.limit,
                        with_vectors,
                        fmt,
                    )
                )
            except Exception as exc:
                failures += 1
                logger.error("snapshot_failed collection=%s err=%s", name, exc)
                # Retry once after a short backoff — big collections can
                # transiently disconnect from Qdrant mid-snapshot.
                try:
                    logger.info("snapshot_retry collection=%s", name)
                    time.sleep(5)
                    snapshot_collection(
                        client,
                        b2_api,
                        bucket_name,
                        date_str,
                        name,
                        workdir,
                        args.limit,
                        with_vectors,
                        fmt,
                    )
                    failures -= 1
                    logger.info("snapshot_retry_ok collection=%s", name)
                except Exception as retry_exc:
                    logger.error("snapshot_retry_failed collection=%s err=%s", name, retry_exc)

    try:
        if entries:
            upload_manifest(b2_api, bucket_name, date_str, entries)
    except Exception as exc:
        failures += 1
        logger.error("manifest_failed: %s", exc)

    # Retention pruning — gated: only runs with --prune; dry-run unless
    # --prune-live AND NOT --dry-run (--dry-run already returned above).
    if args.prune:
        try:
            prune_snapshots(
                b2_api,
                bucket_name,
                dry_run=not args.prune_live,
                keep_daily=args.keep_daily,
                keep_weekly=args.keep_weekly,
                keep_monthly=args.keep_monthly,
            )
        except Exception as exc:
            failures += 1
            logger.error("prune_failed: %s", exc)

    total_points = sum(int(e.get("point_count", 0)) for e in entries)
    logger.info(
        "=== B2 permanence sync complete: %d ok, %d failed, %d points, %s total ===",
        len(entries),
        failures,
        total_points,
        _fmt_size(sum(int(e.get("bytes_stored", 0)) for e in entries)),
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
