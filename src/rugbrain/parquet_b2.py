"""B2 native-API download helpers for parquet snapshots.

Split out of :mod:`rugbrain.parquet_analytics` (2026-10-25, size ratchet).
httpx-only plumbing (no boto3): credentials → authorize → bucket id → key
listing → object download, plus the local staging helper.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# B2 account's S3-compatible endpoint (from b2_authorize_account → s3ApiUrl).
# Override per-account with B2_S3_ENDPOINT (e.g. s3.us-west-004.backblazeb2.com).
DEFAULT_B2_ENDPOINT = "s3.us-east-005.backblazeb2.com"
DEFAULT_BUCKET = "rmi-rag-permanence"
DEFAULT_B2_API = "api.backblazeb2.com"


def _env_b2_credentials() -> tuple[str, str, str, str]:
    """Return (key_id, application_key, bucket, endpoint) from the environment."""
    key_id = os.environ.get("B2_APPLICATION_KEY_ID", "")
    key = os.environ.get("B2_APPLICATION_KEY", "")
    if not key_id or not key:
        raise RuntimeError(
            "B2 credentials missing — set B2_APPLICATION_KEY_ID and B2_APPLICATION_KEY"
        )
    bucket = os.environ.get("B2_BUCKET_NAME", DEFAULT_BUCKET)
    endpoint = os.environ.get("B2_S3_ENDPOINT", DEFAULT_B2_ENDPOINT)
    return key_id, key, bucket, endpoint


def _b2_native_auth(key_id: str, key: str) -> dict[str, Any]:
    """Authorize against B2's native API (returns apiUrl, token, downloadUrl)."""
    import httpx

    auth = base64.b64encode(f"{key_id}:{key}".encode()).decode()
    # b2_authorize_account is a GET endpoint (no body) per the B2 Native
    # API spec; POSTing a body returns 400 from some B2 API servers.
    resp = httpx.get(
        f"https://{os.environ.get('B2_API_URL', DEFAULT_B2_API)}/b2api/v2/b2_authorize_account",
        headers={"Authorization": f"Basic {auth}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "api_url": data.get("apiUrl", ""),
        "token": data.get("authorizationToken", ""),
        "download_url": data.get("downloadUrl", ""),
        "bucket_id": (data.get("allowed") or {}).get("bucketId", ""),
    }


def _b2_bucket_id(auth: dict[str, Any], bucket: str) -> str:
    """Resolve a bucket name to its B2 bucket id (native API)."""
    import httpx

    if auth.get("bucket_id"):
        return str(auth["bucket_id"])
    account_id = os.environ.get("B2_ACCOUNT_ID", "")
    if not account_id:
        # Master application keys use the account id as the key id.
        account_id = os.environ.get("B2_APPLICATION_KEY_ID", "")
    resp = httpx.post(
        f"{auth['api_url']}/b2api/v2/b2_list_buckets",
        headers={"Authorization": auth["token"]},
        json={"accountId": account_id},
        timeout=30,
    )
    resp.raise_for_status()
    for b in resp.json().get("buckets", []):
        if b.get("bucketName") == bucket:
            return str(b.get("bucketId", ""))
    raise RuntimeError(f"B2 bucket not found: {bucket}")


def _b2_list_parquet_keys(auth: dict[str, Any], bucket: str, date: str | None) -> list[str]:
    """List parquet object keys for a date (or all dates) via native API."""
    import httpx

    bucket_id = _b2_bucket_id(auth, bucket)
    prefix = f"snapshots/{date}/" if date else "snapshots/"
    keys: list[str] = []
    start = ""
    for _ in range(20):
        body: dict[str, Any] = {
            "bucketId": bucket_id,
            "prefix": prefix,
            "maxFileCount": 1000,
        }
        if start:
            body["startFileName"] = start
        resp = httpx.post(
            f"{auth['api_url']}/b2api/v2/b2_list_file_names",
            headers={"Authorization": auth["token"]},
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        for f in data.get("files", []):
            name = f.get("fileName", "")
            if name.endswith(".parquet"):
                keys.append(name)
        if data.get("nextFileName"):
            start = data["nextFileName"]
        else:
            break
    return keys


def _b2_download(auth: dict[str, Any], bucket: str, key: str, dest: Path) -> Path:
    """Download a B2 object over the native API to *dest*."""
    import httpx

    url = f"{auth['download_url']}/file/{bucket}/{key}"
    with httpx.Client(timeout=120) as client:
        resp = client.get(url, headers={"Authorization": auth["token"]})
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return dest


def _download_snapshots_local_into(date: str | None, tmp_dir: str) -> str:
    """Download matching parquet snapshots into *tmp_dir*; return glob path."""
    key_id, key, bucket, _endpoint = _env_b2_credentials()
    auth = _b2_native_auth(key_id, key)
    keys = _b2_list_parquet_keys(auth, bucket, date)
    if not keys:
        raise RuntimeError(f"no parquet snapshots found in {bucket} for date={date or 'any'}")
    dest_dir = Path(tmp_dir)
    for k in keys:
        _b2_download(auth, bucket, k, dest_dir / k.replace("/", "_"))
    logger.info("downloaded %d parquet snapshots to %s", len(keys), dest_dir)
    return str(dest_dir / "*.parquet")
