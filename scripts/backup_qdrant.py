#!/usr/bin/env python3
"""Qdrant snapshot backup to Cloudflare R2 (S3-compatible).

Creates API snapshots of all RAG collections, uploads them to R2 for
off-site durability, and cleans up local temp files.

Credentials (in priority order):
  1. Environment variables: R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_BUCKET
  2. /root/.config/rclone/rclone.conf [r2] section
  3. Hard-coded defaults (for this deployment)

Schedule: 30 3 * * * (daily 3:30am UTC, after DB backups at 2am)
Cron: cd /srv/work/repos/rmi-rag && python3 scripts/backup_qdrant.py >> /var/log/qdrant-backup.log 2>&1
"""

import argparse
import configparser
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import boto3
import requests
from botocore.config import Config as BotoConfig
from qdrant_client import QdrantClient

# ---------------------------------------------------------------------------
# Defaults — override via env or rclone.conf
# ---------------------------------------------------------------------------
R2_ENDPOINT_DEFAULT = "https://8f9bd9165c1250b426c66dc1967deefd.r2.cloudflarestorage.com"
R2_BUCKET_DEFAULT = "rag-backup"
R2_KEY_PREFIX = "qdrant-snapshots"
RCLONE_CONF_PATH = "/root/.config/rclone/rclone.conf"
QDRANT_HOST_DEFAULT = "rmi-qdrant"
QDRANT_PORT_DEFAULT = 6333

# Retain 3 most recent local snapshots per collection (older ones cleaned up)
LOCAL_RETENTION_COUNT = 3

logger = logging.getLogger("backup_qdrant")


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------
def _read_rclone_creds(profile: str = "r2") -> dict:
    """Read R2 credentials from rclone.conf."""
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
            "access_key": sec.get("access_key_id", ""),
            "secret_key": sec.get("secret_access_key", ""),
            "endpoint": sec.get("endpoint", ""),
        }
    except Exception as exc:
        logger.warning("Failed to read rclone.conf: %s", exc)
        return {}


def _resolve_r2_config() -> dict:
    """Resolve R2 connection config from env → rclone.conf → defaults."""
    # 1. Env overrides
    config = {
        "access_key": os.environ.get("R2_ACCESS_KEY") or "",
        "secret_key": os.environ.get("R2_SECRET_KEY") or "",
        "endpoint": os.environ.get("R2_ENDPOINT") or "",
        "bucket": os.environ.get("R2_BUCKET") or "",
    }

    # 2. Fill missing from rclone.conf
    if not config["access_key"] or not config["secret_key"]:
        rclone = _read_rclone_creds()
        if not config["access_key"]:
            config["access_key"] = rclone.get("access_key", "")
        if not config["secret_key"]:
            config["secret_key"] = rclone.get("secret_key", "")
        if not config["endpoint"]:
            config["endpoint"] = rclone.get("endpoint", "")

    # 3. Fallback to hard-coded defaults
    if not config["endpoint"]:
        config["endpoint"] = R2_ENDPOINT_DEFAULT
    if not config["bucket"]:
        config["bucket"] = R2_BUCKET_DEFAULT

    return config


# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------
def list_collections(client: QdrantClient) -> list:
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
        snap_name = snap.name
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

    for coll, files in by_collection.items():
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            try:
                old.unlink()
                logger.debug("Cleaned local snapshot: %s", old.name)
            except OSError as exc:
                logger.warning("Cleanup failed for %s: %s", old.name, exc)


# ---------------------------------------------------------------------------
# S3 upload (R2)
# ---------------------------------------------------------------------------
def upload_to_r2(
    local_path: str,
    r2_config: dict,
    bucket: str,
    key_prefix: str,
) -> bool:
    """Upload a local file to R2 under ``{key_prefix}/{basename}``.

    Returns ``True`` on success.
    """
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=r2_config["endpoint"],
            region_name="auto",
            aws_access_key_id=r2_config["access_key"],
            aws_secret_access_key=r2_config["secret_key"],
            config=BotoConfig(
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "adaptive"},
            ),
        )
        key = f"{key_prefix}/{os.path.basename(local_path)}"
        s3.upload_file(local_path, bucket, key)
        size = os.path.getsize(local_path)
        logger.info("Upload OK    bucket=%s key=%s size=%s", bucket, key, _fmt_size(size))
        return True
    except Exception as exc:
        logger.error("Upload FAILED  bucket=%s key=%s  error=%s", bucket, key, exc)
        return False


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _fmt_size(bytes_: int) -> str:
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
        description="Qdrant snapshot backup to Cloudflare R2",
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
        help="R2 bucket name (overrides env/rclone)",
    )
    p.add_argument(
        "--key-prefix",
        default=R2_KEY_PREFIX,
        help=f"Key prefix in R2 bucket (default: {R2_KEY_PREFIX})",
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
    # Quiet the noisy boto3/qdrant loggers
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("qdrant_client").setLevel(logging.WARNING)

    logger.info("=== Qdrant backup started ===")
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
    # Resolve R2 config
    # ------------------------------------------------------------------
    r2_config = _resolve_r2_config()
    bucket = args.bucket or r2_config["bucket"]
    if not bucket:
        logger.critical("No R2 bucket configured — set R2_BUCKET env or --bucket")
        return 1

    logger.info("R2 endpoint: %s", r2_config["endpoint"])
    logger.info("R2 bucket:   %s", bucket)
    logger.info("Key prefix:  %s", args.key_prefix)

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

        # 2. Upload to R2
        ok = upload_to_r2(local_path, r2_config, bucket, args.key_prefix)
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

        try:
            shutil.rmtree(dest_dir, ignore_errors=True)
        except Exception:
            pass

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
