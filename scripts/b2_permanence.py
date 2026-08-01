#!/usr/bin/env python3
"""Nightly B2 permanence sync — Qdrant snapshots to Backblaze B2.

Exports every Qdrant collection as compressed, optionally encrypted
NDJSON and uploads it to B2 under ``snapshots/YYYY-MM-DD/``:

    b2://{B2_BUCKET_NAME}/snapshots/{date}/{collection}.jsonl.zst[.enc]

A ``manifest.json`` describing every collection (point count, file size,
sha256, timestamps, vector metadata) is uploaded alongside the data and
a ``snapshots/latest.json`` pointer records the most recent date.

Data lifecycle this serves:
    Hot  (Qdrant): last 90 days, actively searched
    Cold (B2):     everything, forever, nightly snapshots
    Pruning:       documents older than 90 days are moved to B2 only
                   (kept in Qdrant if high severity) — see
                   ``src/rmi_rag/qdrant_store.py::prune_old_documents``

Usage:
    python scripts/b2_permanence.py --dry-run
    python scripts/b2_permanence.py --collection threat_intel --limit 10
    python scripts/b2_permanence.py --no-encrypt --limit 50
    python scripts/b2_permanence.py --env-file /srv/secrets/b2-permanence.env

Environment:
    B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY   (required)
    B2_BUCKET_NAME                              (default rmi-rag-permanence)
    B2_ENCRYPTION_KEY                           64-char hex (AES-256-GCM)
    B2_ENCRYPTION_ENABLED                       auto|1|0 (default auto)
    QDRANT_URL / QDRANT_PORT                    Qdrant connection

See scripts/b2_common.py for the file/encryption format. Restore:
scripts/b2_restore.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
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
    ENCRYPTED_SUFFIX,
    NDJSON_SUFFIX,
    compress_file,
    encryption_enabled,
    ensure_bucket,
    env_or,
    get_b2_api,
    get_encryption_key,
    load_env_file,
    sha256_file,
    upload_file,
)

logger = logging.getLogger("b2_permanence")

# Qdrant fallback chain (mirrors src/rmi_rag/qdrant_store.py)
DEFAULT_QDRANT_HOST = os.getenv("QDRANT_URL", "http://rmi-qdrant:6333")
DEFAULT_QDRANT_PORT = os.getenv("QDRANT_PORT", "6333")

MANIFEST_NAME = "manifest.json"
LATEST_NAME = "latest.json"
SNAPSHOT_PREFIX = "snapshots"
SCROLL_BATCH = 1000


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
    offset: object = None
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
    encrypt: bool,
) -> dict[str, Any]:
    """Export, compress, encrypt (optional) and upload one collection.

    Returns the manifest entry for this collection.
    """
    base = workdir / name
    base.mkdir(parents=True, exist_ok=True)
    ndjson_path = base / f"{name}{NDJSON_SUFFIX}"
    zst_path = base / f"{name}{NDJSON_SUFFIX}{COMPRESSED_SUFFIX}"

    started = datetime.now(UTC)
    point_count = export_collection_ndjson(
        client, name, ndjson_path, limit=limit, with_vectors=with_vectors
    )

    compress_file(ndjson_path, zst_path, level=19)
    upload_path = zst_path
    suffix = f"{NDJSON_SUFFIX}{COMPRESSED_SUFFIX}"

    if encrypt:
        enc_path = base / f"{name}{NDJSON_SUFFIX}{COMPRESSED_SUFFIX}{ENCRYPTED_SUFFIX}"
        b2_common.encrypt_file(zst_path, enc_path, get_encryption_key().hex())
        upload_path = enc_path
        suffix = f"{NDJSON_SUFFIX}{COMPRESSED_SUFFIX}{ENCRYPTED_SUFFIX}"

    object_key = f"{SNAPSHOT_PREFIX}/{date_str}/{name}{suffix}"
    upload_file(b2_api, bucket_name, upload_path, object_key)

    finished = datetime.now(UTC)
    entry: dict[str, Any] = {
        "collection": name,
        "object_key": object_key,
        "point_count": point_count,
        "bytes_raw": ndjson_path.stat().st_size,
        "bytes_stored": upload_path.stat().st_size,
        "compression": "zstd-19",
        "encrypted": encrypt,
        "sha256": sha256_file(upload_path),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
    }
    if with_vectors:
        entry["vectors_included"] = True
    logger.info(
        "snapshot_ok collection=%s points=%d stored=%s key=%s",
        name,
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
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "date": date_str,
        "encryption": {
            "algorithm": "AES-256-GCM",
            "key_env": "B2_ENCRYPTION_KEY",
            "enabled": any(e.get("encrypted") for e in entries),
        },
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


# ── CLI ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Nightly Qdrant → B2 permanence sync (compressed + encrypted)",
    )
    p.add_argument("--dry-run", action="store_true", help="Do everything except upload")
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
    p.add_argument("--no-encrypt", action="store_true", help="Disable AES-256-GCM encryption")
    p.add_argument(
        "--no-vectors", action="store_true", help="Skip vectors in export (payload only)"
    )
    p.add_argument("--qdrant-url", default=None, help="Qdrant URL override")
    p.add_argument("--qdrant-port", type=int, default=None, help="Qdrant port override")
    p.add_argument("--b2-bucket", default=None, help="B2 bucket override")
    p.add_argument(
        "--env-file",
        default=None,
        help="Load KEY=value env file (e.g. /srv/secrets/b2-permanence.env)",
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
    encrypt = encryption_enabled() and not args.no_encrypt

    # Fail closed on encryption: backups must be encrypted unless the
    # operator EXPLICITLY opts out with --no-encrypt. Never silently
    # upload plaintext because B2_ENCRYPTION_KEY is missing.
    if args.no_encrypt:
        logger.warning(
            "ENCRYPTION DISABLED via --no-encrypt — snapshots will be uploaded "
            "to B2 in PLAINTEXT (compressed only). Do not use in production."
        )
    elif not encrypt:
        logger.critical(
            "Refusing to run: B2_ENCRYPTION_KEY is not set and encryption is "
            "disabled. Backups would be uploaded unencrypted. Set "
            "B2_ENCRYPTION_KEY (64-char hex) or explicitly pass --no-encrypt "
            "to allow plaintext backups."
        )
        return 2

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
        "bucket=%s date=%s collections=%d encrypt=%s vectors=%s",
        bucket_name,
        date_str,
        len(collections),
        encrypt,
        with_vectors,
    )

    if args.dry_run:
        for c in collections:
            info = collection_info(client, c)
            logger.info(
                "dry-run would snapshot collection=%s points=<all> vector_size=%s",
                c,
                info.get("vector_size"),
            )
        logger.info("=== dry-run complete (no uploads) ===")
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
                        encrypt,
                    )
                )
            except Exception as exc:
                failures += 1
                logger.error("snapshot_failed collection=%s err=%s", name, exc)

    try:
        if entries:
            upload_manifest(b2_api, bucket_name, date_str, entries)
    except Exception as exc:
        failures += 1
        logger.error("manifest_failed: %s", exc)

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
