#!/usr/bin/env python3
"""Migrate legacy NDJSON B2 permanence snapshots to parquet.

Downloads the ``*.jsonl.zst[.enc]`` snapshots written by older
``b2_permanence.py`` runs, converts each to a columnar parquet file
(zstd level 19), and uploads it alongside the legacy object:

    snapshots/{YYYY-MM-DD}/{collection}.jsonl.zst[.enc]   (kept — fallback)
    snapshots/{YYYY-MM-DD}/{collection}.parquet           (new)

Legacy objects are NEVER deleted: parquet becomes the queryable
standard while the old files stay as a fallback. The date's
``manifest.json`` is re-uploaded with both entry types so
``b2_restore.py`` prefers the parquet copy (see ``_prefer_format``).

This is the ONLY tool that still reads the pre-2026-08-01 client-side
AES-256-GCM ``.enc`` objects (via the deprecated
``b2_common.decrypt_file``). New uploads are never client-side
encrypted — B2 SSE-B2 (AES256) encrypts them at rest.

Usage:
    python scripts/migrate_to_parquet.py --date 2026-08-01
    python scripts/migrate_to_parquet.py --date 2026-08-01 --collection threat_intel_v2
    python scripts/migrate_to_parquet.py --date 2026-08-01 --dry-run
    python scripts/migrate_to_parquet.py --env-file /path/to/b2-permanence.env

Environment:
    B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY   (required)
    B2_BUCKET_NAME                              (default rmi-rag-permanence)
    B2_ENCRYPTION_KEY                           DEPRECATED — SSE-B2 encrypts
                                                at rest; needed only when
                                                converting legacy .enc objects
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from b2sdk.v2 import B2Api

sys.path.insert(0, str(Path(__file__).resolve().parent))

import b2_common
from b2_common import (
    COMPRESSED_SUFFIX,
    ENCRYPTED_SUFFIX,
    NDJSON_SUFFIX,
    PARQUET_SUFFIX,
    ensure_bucket,
    env_or,
    get_b2_api,
    get_encryption_key,
    list_object_keys,
    load_env_file,
    sha256_file,
    upload_file,
    write_parquet_file,
)

logger = logging.getLogger("migrate_to_parquet")

MANIFEST_NAME = "manifest.json"
SNAPSHOT_PREFIX = "snapshots"
MANIFEST_SCHEMA_VERSION = 2


# ── Conversion ─────────────────────────────────────────────────────────


def ndjson_records_to_rows(path: Path) -> list[dict[str, object]]:
    """Parse a legacy NDJSON snapshot into parquet row dicts."""
    rows: list[dict[str, object]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            payload = rec.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            vector = rec.get("vector")
            row: dict[str, object] = {
                "id": str(rec.get("id") or ""),
                "content": str(payload.get("content") or ""),
                "metadata": json.dumps(payload.get("metadata") or {}, default=str),
                "payload": json.dumps(payload, default=str),
                "embedding": None,
                "vectors": None,
            }
            if isinstance(vector, list):
                row["embedding"] = [float(x) for x in vector]
            elif isinstance(vector, dict):
                dense = vector.get("dense")
                if isinstance(dense, list):
                    row["embedding"] = [float(x) for x in dense]
                row["vectors"] = json.dumps(vector, default=str)
            rows.append(row)
    return rows


def legacy_object_format(object_key: str) -> str | None:
    """Return the storage format of a legacy snapshot object, else None.

    Handles both ``.jsonl.zst`` and ``.jsonl.zst.enc`` suffixes and the
    plain ``.jsonl`` suffix. Parquet objects return None (not legacy).
    """
    key = object_key
    if key.endswith(ENCRYPTED_SUFFIX):
        key = key[: -len(ENCRYPTED_SUFFIX)]
    if key.endswith(COMPRESSED_SUFFIX):
        return "zstd-encrypted" if object_key.endswith(ENCRYPTED_SUFFIX) else "zstd"
    if key.endswith(NDJSON_SUFFIX):
        return "plain"
    return None


def collection_from_legacy_key(object_key: str) -> str:
    """Extract the collection name from a legacy snapshot object key."""
    name = Path(object_key).name
    for suffix in (
        f"{NDJSON_SUFFIX}{COMPRESSED_SUFFIX}{ENCRYPTED_SUFFIX}",
        f"{NDJSON_SUFFIX}{COMPRESSED_SUFFIX}",
        NDJSON_SUFFIX,
    ):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem.split(".")[0]


# ── Migration ──────────────────────────────────────────────────────────


def convert_one(
    b2_api: B2Api,
    bucket_name: str,
    date_str: str,
    object_key: str,
    workdir: Path,
) -> dict[str, Any]:
    """Convert one legacy snapshot object to parquet and upload it.

    Returns the new parquet manifest entry (or {} when skipped).
    """
    legacy_fmt = legacy_object_format(object_key)
    if legacy_fmt is None:
        logger.info("skip_not_legacy key=%s", object_key)
        return {}

    collection = collection_from_legacy_key(object_key)
    new_key = f"{SNAPSHOT_PREFIX}/{date_str}/{collection}{PARQUET_SUFFIX}"

    if new_key in list_object_keys(b2_api, bucket_name, prefix=new_key):
        logger.info("skip_already_parquet collection=%s key=%s", collection, new_key)
        return {}

    logger.info("convert collection=%s legacy=%s key=%s", collection, legacy_fmt, object_key)

    local_raw = workdir / object_key.replace("/", "_")
    b2_common.download_file(b2_api, bucket_name, object_key, local_raw)
    legacy_bytes = local_raw.stat().st_size

    path: Path = local_raw
    if object_key.endswith(ENCRYPTED_SUFFIX):
        logger.warning(
            "legacy_enc_object key=%s: decrypting with deprecated client-side key "
            "(B2 SSE-B2 now encrypts at rest; this path is for pre-2026-08-01 "
            "snapshots only)",
            object_key,
        )
        dec = workdir / (local_raw.name + ".zst")
        b2_common.decrypt_file(local_raw, dec, get_encryption_key().hex())
        path = dec
    ndjson = workdir / (path.name + NDJSON_SUFFIX)
    if path.name.endswith(COMPRESSED_SUFFIX) or path.name.endswith(ENCRYPTED_SUFFIX):
        b2_common.decompress_file(path, ndjson)
    else:
        ndjson = path

    rows = ndjson_records_to_rows(ndjson)
    parquet_path = workdir / f"{collection}{PARQUET_SUFFIX}"
    write_parquet_file(parquet_path, rows, level=19)

    started = datetime.now(UTC)
    upload_file(b2_api, bucket_name, parquet_path, new_key)
    finished = datetime.now(UTC)

    entry: dict[str, Any] = {
        "collection": collection,
        "format": "parquet",
        "object_key": new_key,
        "point_count": len(rows),
        "bytes_raw": ndjson.stat().st_size if ndjson.exists() else parquet_path.stat().st_size,
        "bytes_stored": parquet_path.stat().st_size,
        "compression": "parquet-zstd-19",
        "encrypted": False,
        "sha256": sha256_file(parquet_path),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "migrated_from": object_key,
        "legacy_bytes": legacy_bytes,
    }
    logger.info(
        "converted collection=%s points=%d legacy=%s parquet=%s ratio=%.2f",
        collection,
        len(rows),
        _fmt_size(legacy_bytes),
        _fmt_size(parquet_path.stat().st_size),
        parquet_path.stat().st_size / max(1, legacy_bytes),
    )
    return entry


def update_manifest(
    b2_api: B2Api,
    bucket_name: str,
    date_str: str,
    legacy_entries: list[dict[str, Any]],
    parquet_entries: list[dict[str, Any]],
) -> None:
    """Merge migrated parquet entries into the date's manifest and re-upload."""
    old_key = f"{SNAPSHOT_PREFIX}/{date_str}/{MANIFEST_NAME}"
    manifest: dict[str, Any] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="b2-migrate-manifest-") as tmp:
            local = Path(tmp) / MANIFEST_NAME
            b2_common.download_file(b2_api, bucket_name, old_key, local)
            loaded = json.loads(local.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
    except Exception as exc:
        logger.warning("no existing manifest for %s: %s", date_str, exc)

    existing = manifest.get("collections", [])
    if not isinstance(existing, list):
        existing = []

    by_key: dict[str, dict[str, Any]] = {str(e.get("object_key", "")): e for e in existing}
    for e in legacy_entries:
        by_key.setdefault(str(e.get("object_key", "")), e)
    for e in parquet_entries:
        by_key[str(e.get("object_key", ""))] = e

    manifest["schema_version"] = MANIFEST_SCHEMA_VERSION
    manifest["created_at"] = datetime.now(UTC).isoformat()
    manifest["date"] = date_str
    manifest["collections"] = list(by_key.values())
    manifest["total_points"] = sum(int(e.get("point_count", 0)) for e in manifest["collections"])
    manifest["formats"] = sorted({str(e.get("format", "ndjson")) for e in manifest["collections"]})
    manifest.setdefault("encryption", {}).setdefault(
        "note",
        "B2 SSE-B2 (AES256) encrypts all objects at rest; legacy NDJSON .enc copies "
        "also carry the deprecated pre-2026-08-01 client-side AES-256-GCM layer.",
    )

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(manifest, tmp, indent=2)
        tmp_path = Path(tmp.name)
    try:
        upload_file(b2_api, bucket_name, tmp_path, old_key)
    finally:
        tmp_path.unlink(missing_ok=True)
    logger.info("manifest_updated date=%s entries=%d", date_str, len(manifest["collections"]))


# ── CLI ────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Convert legacy NDJSON B2 permanence snapshots to parquet",
    )
    p.add_argument("--date", required=True, help="Snapshot date YYYY-MM-DD")
    p.add_argument(
        "--collection",
        action="append",
        default=[],
        help="Restrict to a collection (repeatable; default: all legacy objects)",
    )
    p.add_argument("--dry-run", action="store_true", help="Report only, no downloads/uploads")
    p.add_argument("--b2-bucket", default=None, help="B2 bucket override")
    p.add_argument("--env-file", default=None, help="Load KEY=value env file")
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
        b2_api = get_b2_api()
    except Exception as exc:
        logger.critical("b2_auth_failed: %s", exc)
        return 2

    bucket_name = args.b2_bucket or env_or("B2_BUCKET_NAME", b2_common.DEFAULT_BUCKET)
    ensure_bucket(b2_api, bucket_name)

    prefix = f"{SNAPSHOT_PREFIX}/{args.date}/"
    keys = [
        k
        for k in list_object_keys(b2_api, bucket_name, prefix=prefix)
        if not k.endswith(MANIFEST_NAME) and legacy_object_format(k) is not None
    ]
    if args.collection:
        wanted = set(args.collection)
        keys = [k for k in keys if collection_from_legacy_key(k) in wanted]

    if not keys:
        logger.warning("no legacy snapshots to migrate for date=%s", args.date)
        return 0

    logger.info(
        "=== parquet migration started: date=%s legacy_objects=%d ===",
        args.date,
        len(keys),
    )

    if args.dry_run:
        for k in sorted(keys):
            logger.info("dry-run would convert key=%s", k)
        logger.info("=== dry-run complete (no downloads/uploads) ===")
        return 0

    legacy_entries: list[dict[str, Any]] = []
    parquet_entries: list[dict[str, Any]] = []
    failures = 0
    with tempfile.TemporaryDirectory(prefix="b2-migrate-") as workdir_str:
        workdir = Path(workdir_str)
        for idx, key in enumerate(sorted(keys), 1):
            logger.info("[%d/%d] %s", idx, len(keys), key)
            try:
                entry = convert_one(b2_api, bucket_name, args.date, key, workdir)
                if entry:
                    parquet_entries.append(entry)
            except Exception as exc:
                failures += 1
                logger.error("convert_failed key=%s err=%s", key, exc)

    try:
        update_manifest(b2_api, bucket_name, args.date, legacy_entries, parquet_entries)
    except Exception as exc:
        failures += 1
        logger.error("manifest_failed: %s", exc)

    total_legacy = sum(int(e.get("legacy_bytes", 0)) for e in parquet_entries)
    total_parquet = sum(int(e.get("bytes_stored", 0)) for e in parquet_entries)
    logger.info(
        "=== migration complete: %d converted, %d failed, legacy=%s parquet=%s "
        "overall_ratio=%.2f (legacy copies kept on B2) ===",
        len(parquet_entries),
        failures,
        _fmt_size(total_legacy),
        _fmt_size(total_parquet),
        total_parquet / max(1, total_legacy),
    )
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
