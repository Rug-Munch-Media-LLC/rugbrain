#!/usr/bin/env python3
"""Qdrant snapshot backup to Backblaze B2.

Creates API snapshots of all RAG collections, uploads them to B2 for
off-site durability, and cleans up local temp files.

Migrated from Cloudflare R2 to Backblaze B2 on 2026-07-31.

Credentials (in priority order):
  1. Environment variables: B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY, B2_BUCKET_NAME
  2. --env-file (defaults to /path/to/b2-permanence.env if present)
  3. rclone.conf [rugmunch-b2] section (account/key)

B2 uses the native B2 API (via b2sdk) — the S3-compatible API is not
enabled for the master application key.

Schedule: 30 3 * * * (daily 3:30am UTC, after DB backups at 2am)
Cron: cd /path/to/rugbrain && python3 scripts/backup_qdrant.py >> /var/log/qdrant-backup.log 2>&1
"""

import argparse
import configparser
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from rugbrain.dim_drift import DEFAULT_QDRANT_URL

import requests
from qdrant_client import QdrantClient

# Allow importing the shared B2 helper module (scripts/b2_common.py)
sys.path.insert(0, str(Path(__file__).resolve().parent))

import contextlib

from b2_common import ensure_bucket, get_b2_api, list_object_keys, load_env_file, upload_file
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Defaults — override via env or --env-file
# ---------------------------------------------------------------------------
B2_BUCKET_DEFAULT = "rmi-rag-permanence"
B2_KEY_PREFIX = "qdrant-snapshots"
ENV_FILE_DEFAULT = "/path/to/b2-permanence.env"
RCLONE_CONF_PATH = os.getenv("RCLONE_CONF", "~/.config/rclone/rclone.conf")
_QDRANT_DEFAULT = urlparse(os.getenv("QDRANT_URL", DEFAULT_QDRANT_URL))
QDRANT_HOST_DEFAULT = _QDRANT_DEFAULT.hostname or "rmi-qdrant"
QDRANT_PORT_DEFAULT = _QDRANT_DEFAULT.port or 6334

# Retain 3 most recent local snapshots per collection (older ones cleaned up)
LOCAL_RETENTION_COUNT = 3

logger = logging.getLogger("backup_qdrant")


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------
def _read_rclone_creds(profile: str = "rugmunch-b2") -> dict[str, Any]:
    """Read B2 credentials from rclone.conf (account → key ID, key → app key)."""
    path = Path(RCLONE_CONF_PATH)
    if not path.exists():
        return {}
    try:
        cfg = configparser.ConfigParser()
        cfg.read(str(path))
        if profile not in cfg:
            logger.warning("Profile [%s] not found in %s", profile, RCLONE_CONF_PATH)
            return {}
        sec = cfg[profile]
        return {
            "key_id": sec.get("account", ""),
            "app_key": sec.get("key", ""),
        }
    except Exception as exc:
        logger.warning("Failed to read rclone.conf: %s", exc)
        return {}


def _resolve_b2_config() -> dict[str, Any]:
    """Resolve B2 connection config from env → env-file → rclone.conf."""
    # 1. Env overrides
    config = {
        "key_id": os.environ.get("B2_APPLICATION_KEY_ID") or "",
        "app_key": os.environ.get("B2_APPLICATION_KEY") or "",
        "bucket": os.environ.get("B2_BUCKET_NAME") or "",
    }

    # 2. Fill missing from env file (deployed secrets)
    if not config["key_id"] or not config["app_key"]:
        env_path = Path(ENV_FILE_DEFAULT)
        if env_path.exists():
            try:
                load_env_file(str(env_path))
                config["key_id"] = config["key_id"] or os.environ.get("B2_APPLICATION_KEY_ID", "")
                config["app_key"] = config["app_key"] or os.environ.get("B2_APPLICATION_KEY", "")
                config["bucket"] = config["bucket"] or os.environ.get("B2_BUCKET_NAME", "")
            except Exception as exc:
                logger.warning("Failed to load env file %s: %s", env_path, exc)

    # 3. Fill missing from rclone.conf [rugmunch-b2]
    if not config["key_id"] or not config["app_key"]:
        rclone = _read_rclone_creds()
        config["key_id"] = config["key_id"] or rclone.get("key_id", "")
        config["app_key"] = config["app_key"] or rclone.get("app_key", "")

    # 4. Fallback to default bucket
    if not config["bucket"]:
        config["bucket"] = B2_BUCKET_DEFAULT

    return config


# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------
def list_collections(client: QdrantClient) -> list[str]:
    """Return sorted list of collection names."""
    return sorted(c.name for c in client.get_collections().collections)


def snapshot_collection(
    client: QdrantClient, name: str, dest_dir: str, qdrant_host: str, qdrant_port: int
) -> str | None:
    """Create a Qdrant snapshot and download it to *dest_dir* via REST API.

    Returns the local file path on success, or ``None`` on failure.
    The file is named ``{name}_{date}.snapshot``.
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    try:
        snap = client.create_snapshot(collection_name=name)
        snap_name = snap.name if snap else ""
        if not snap_name:
            return None
        local_path = os.path.join(dest_dir, f"{name}_{date_str}.snapshot")

        # Download via REST API (download_snapshot not available in this client version)
        url = f"http://{qdrant_host}:{qdrant_port}/collections/{name}/snapshots/{snap_name}"
        with requests.get(url, stream=True, timeout=300) as resp:
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
                    if chunk:
                        f.write(chunk)

        size = os.path.getsize(local_path)
        logger.info(
            "Snapshot OK  collection=%s size=%s name=%s",
            name,
            _fmt_size(size),
            snap_name,
        )
        return local_path
    except Exception as exc:
        logger.error("Snapshot FAILED  collection=%s  error=%s", name, exc)
        return None


def cleanup_local_snapshots(dest_dir: str, keep: int = LOCAL_RETENTION_COUNT) -> None:
    """Remove older local snapshot files, keeping the *keep* most recent per
    collection prefix."""
    import collections

    by_collection = collections.defaultdict(list)
    for f in Path(dest_dir).glob("*.snapshot"):
        # filenames are: {name}_{date}.snapshot
        parts = f.stem.rsplit("_", 1)  # split on last underscore
        coll_name = parts[0] if len(parts) == 2 else f.stem
        by_collection[coll_name].append(f)

    for _coll, files in by_collection.items():
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            try:
                old.unlink()
                logger.debug("Cleaned local snapshot: %s", old.name)
            except OSError as exc:
                logger.warning("Cleanup failed for %s: %s", old.name, exc)


# ---------------------------------------------------------------------------
# B2 upload
# ---------------------------------------------------------------------------
def upload_to_b2(
    local_path: str,
    b2_config: dict[str, Any],
    bucket: str,
    key_prefix: str,
) -> bool:
    """Upload a local file to B2 under ``{key_prefix}/{basename}``.

    Returns ``True`` on success.
    """
    key = ""
    try:
        api = get_b2_api()
        ensure_bucket(api, bucket)
        key = f"{key_prefix}/{os.path.basename(local_path)}"
        upload_file(api, bucket, Path(local_path), key)
        size = os.path.getsize(local_path)
        logger.info("Upload OK    bucket=%s key=%s size=%s", bucket, key, _fmt_size(size))
        return True
    except Exception as exc:
        logger.error("Upload FAILED  bucket=%s key=%s  error=%s", bucket, key, exc)
        return False


def test_b2(b2_config: dict[str, Any]) -> int:
    """Connect to B2 and list bucket contents (no Qdrant required)."""
    try:
        api = get_b2_api()
        ensure_bucket(api, b2_config["bucket"])
        keys = list_object_keys(api, b2_config["bucket"])
        logger.info("B2 connection OK  bucket=%s objects=%d", b2_config["bucket"], len(keys))
        for k in keys[:100]:
            print("  " + k)
        if len(keys) > 100:
            print(f"  ... and {len(keys) - 100} more")
        print(f"B2 OK: bucket={b2_config['bucket']} objects={len(keys)}")
        return 0
    except Exception as exc:
        logger.error("B2 connection FAILED: %s", exc)
        print(f"B2 FAILED: {exc}")
        return 1


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _fmt_size(bytes_: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_ < 1024:
            return f"{bytes_:.1f}{unit}"
        bytes_ /= 1024
    return f"{bytes_:.1f}TB"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Qdrant snapshot backup to Backblaze B2",
    )
    p.add_argument(
        "--qdrant-host",
        default=QDRANT_HOST_DEFAULT,
        help=f"Qdrant gRPC/REST host (default: {QDRANT_HOST_DEFAULT})",
    )
    p.add_argument(
        "--qdrant-port",
        type=int,
        default=QDRANT_PORT_DEFAULT,
        help=f"Qdrant port (default: {QDRANT_PORT_DEFAULT})",
    )
    p.add_argument(
        "--bucket",
        default="",
        help="B2 bucket name (overrides env/env-file)",
    )
    p.add_argument(
        "--key-prefix",
        default=B2_KEY_PREFIX,
        help=f"Key prefix in B2 bucket (default: {B2_KEY_PREFIX})",
    )
    p.add_argument(
        "--dest-dir",
        default="",
        help="Local directory for snapshot downloads (default: temp dir)",
    )
    p.add_argument(
        "--keep-local",
        type=int,
        default=LOCAL_RETENTION_COUNT,
        help=f"Number of recent local snapshots to keep per collection (default: {LOCAL_RETENTION_COUNT})",
    )
    p.add_argument(
        "--env-file",
        default="",
        help=f"Load KEY=value env file with B2 credentials (default: {ENV_FILE_DEFAULT} if present)",
    )
    p.add_argument(
        "--test",
        action="store_true",
        help="Connect to B2 and list bucket contents, then exit (no Qdrant needed)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="List collections and print what would be done without doing it",
    )
    p.add_argument(
        "--list-collections",
        action="store_true",
        help="List all Qdrant collections and exit",
    )
    p.add_argument(
        "--max-collections",
        type=int,
        default=0,
        help="Limit the number of collections to process (for testing)",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    # Quiet the noisy b2sdk/qdrant loggers
    logging.getLogger("b2sdk").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("qdrant_client").setLevel(logging.WARNING)

    # ------------------------------------------------------------------
    # Load env file (credentials) if explicitly requested or default exists
    # ------------------------------------------------------------------
    env_path = args.env_file or ENV_FILE_DEFAULT
    if env_path and Path(env_path).exists():
        try:
            load_env_file(env_path)
            logger.info("Loaded env file: %s", env_path)
        except Exception as exc:
            logger.warning("Failed to load env file %s: %s", env_path, exc)

    logger.info("=== Qdrant backup started ===")

    # ------------------------------------------------------------------
    # Resolve B2 config
    # ------------------------------------------------------------------
    b2_config = _resolve_b2_config()
    bucket = args.bucket or b2_config["bucket"]
    if not bucket:
        logger.critical("No B2 bucket configured — set B2_BUCKET_NAME env or --bucket")
        return 1

    logger.info("B2 bucket:   %s", bucket)
    logger.info("Key prefix:  %s", args.key_prefix)

    # ------------------------------------------------------------------
    # B2 connectivity test (--test) — no Qdrant required
    # ------------------------------------------------------------------
    if args.test:
        return test_b2(b2_config)

    logger.info("Host: %s:%s", args.qdrant_host, args.qdrant_port)

    # ------------------------------------------------------------------
    # Connect to Qdrant
    # ------------------------------------------------------------------
    try:
        client = QdrantClient(host=args.qdrant_host, port=args.qdrant_port)
        # Quick health check
        _ = client.get_collections()
    except Exception as exc:
        logger.critical(
            "Cannot connect to Qdrant at %s:%s — %s", args.qdrant_host, args.qdrant_port, exc
        )
        return 1
    logger.info("Connected to Qdrant")

    # ------------------------------------------------------------------
    # List collections
    # ------------------------------------------------------------------
    try:
        collections = list_collections(client)
    except Exception as exc:
        logger.critical("Failed to list collections: %s", exc)
        return 1

    logger.info("Found %d collection(s): %s", len(collections), ", ".join(collections))

    if args.list_collections:
        for c in collections:
            print(c)
        return 0

    if not collections:
        logger.warning("No collections to back up")
        return 0

    # ------------------------------------------------------------------
    # Destination directory
    # ------------------------------------------------------------------
    if args.dest_dir:
        dest_dir = args.dest_dir
        os.makedirs(dest_dir, exist_ok=True)
    else:
        dest_dir = tempfile.mkdtemp(prefix="qdrant-backup-")
        logger.info("Temp dir: %s", dest_dir)

    # ------------------------------------------------------------------
    # Apply --max-collections limit
    # ------------------------------------------------------------------
    to_process = collections
    if args.max_collections and args.max_collections < len(to_process):
        to_process = to_process[: args.max_collections]
        logger.info("Limited to %d collection(s) (--max-collections)", len(to_process))

    # ------------------------------------------------------------------
    # Process each collection
    # ------------------------------------------------------------------
    success_count = 0
    fail_count = 0
    total_bytes = 0

    for idx, coll_name in enumerate(to_process, 1):
        logger.info("[%d/%d] Processing collection: %s", idx, len(to_process), coll_name)

        if args.dry_run:
            logger.info("  DRY-RUN: would snapshot + upload %s", coll_name)
            continue

        # 1. Create + download snapshot
        local_path = snapshot_collection(
            client, coll_name, dest_dir, args.qdrant_host, args.qdrant_port
        )
        if local_path is None:
            fail_count += 1
            continue

        # 2. Upload to B2
        ok = upload_to_b2(local_path, b2_config, bucket, args.key_prefix)
        if ok:
            total_bytes += os.path.getsize(local_path)
            success_count += 1
        else:
            fail_count += 1

    # ------------------------------------------------------------------
    # Local cleanup (keep N most recent per collection)
    # ------------------------------------------------------------------
    if not args.dry_run:
        cleanup_local_snapshots(dest_dir, keep=args.keep_local)

    # ------------------------------------------------------------------
    # Clean up temp dir unless user specified one
    # ------------------------------------------------------------------
    if not args.dest_dir:
        import shutil

        with contextlib.suppress(Exception):
            shutil.rmtree(dest_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    logger.info(
        "=== Qdrant backup complete: %d succeeded, %d failed, %s total ===",
        success_count,
        fail_count,
        _fmt_size(total_bytes) if not args.dry_run else "N/A (dry-run)",
    )
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
