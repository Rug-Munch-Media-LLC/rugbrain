"""DuckDB analytics over RAG permanence snapshots on Backblaze B2.

Query parquet snapshots written by ``scripts/b2_permanence.py`` with
plain SQL:

    SELECT content, metadata FROM snapshots
    WHERE json_extract_string(metadata, '$.severity') = 'critical';

Two access paths are supported:

1. **Direct-from-B2 (preferred)** — DuckDB's httpfs extension reads
   parquet straight off B2's S3-compatible endpoint, so queries only
   fetch the needed column chunks. Requires a B2 application key with
   *S3-compatible API access* (B2 console → Application Keys → "Allow
   access to the S3 API").
2. **Download fallback** — if S3 auth fails (e.g. the key predates S3
   capability), the parquet files are downloaded over B2's native API
   (httpx, no extra dependencies) and queried locally. Same results,
   just a full download.

The endpoint is exposed as ``POST /api/v1/rag/query`` (see
``src/rmi_rag/endpoints.py``). Credentials are read from the process
environment and are never logged or returned.

Security: the endpoint is behind API-key auth, and ``validate_read_only_sql``
rejects anything except read-only SELECT/WITH/DESCRIBE/SHOW statements.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# B2 account's S3-compatible endpoint (from b2_authorize_account → s3ApiUrl).
# Override per-account with B2_S3_ENDPOINT (e.g. s3.us-west-004.backblazeb2.com).
DEFAULT_B2_ENDPOINT = "s3.us-east-005.backblazeb2.com"
DEFAULT_BUCKET = "rmi-rag-permanence"
DEFAULT_B2_API = "api.backblazeb2.com"

MAX_RESULTS = 1000

# ── Read-only SQL guard ────────────────────────────────────────────────
_ALLOWED_PREFIX = re.compile(r"^\s*(SELECT|WITH|DESCRIBE|SHOW|VALUES)\b", re.IGNORECASE)
_FORBIDDEN_WORD = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|COPY|IMPORT|EXPORT|"
    r"INSTALL|LOAD|CALL|PRAGMA|SET|VACUUM|CHECKPOINT)\b",
    re.IGNORECASE,
)
_EXTERNAL_ACCESS = re.compile(
    r"\b(read_parquet|read_csv|read_json|read_text|read_blob|glob)\s*\(|"
    r"\b(https?://|s3://|file://)\b",
    re.IGNORECASE,
)


def validate_read_only_sql(query: str) -> None:
    """Raise ValueError unless *query* is a single read-only statement.

    Blocks destructive DDL/DML, extension loading, and direct external
    file access (SSRF guard) so a caller can only query the snapshots
    view we expose.
    """
    if not query or not query.strip():
        raise ValueError("query is required")
    if not _ALLOWED_PREFIX.search(query):
        raise ValueError("Only read-only SELECT/WITH/DESCRIBE/SHOW queries are allowed")
    if _FORBIDDEN_WORD.search(query):
        raise ValueError("Query contains a disallowed statement keyword")
    if _EXTERNAL_ACCESS.search(query):
        raise ValueError("Query may not read external files directly — query the `snapshots` view")
    body = query.strip().rstrip(";").strip()
    if ";" in body:
        raise ValueError("Multiple statements are not allowed")


def json_safe_row(row: tuple[Any, ...]) -> list[Any]:
    """Convert one DuckDB row to JSON-serializable Python values."""
    out: list[Any] = []
    for value in row:
        if value is None or isinstance(value, (str, int, bool)):
            out.append(value)
        elif isinstance(value, float):
            out.append(value if value == value else None)  # NaN → null
        elif isinstance(value, (list, tuple)):
            out.append([v if not isinstance(v, float) or v == v else None for v in value])
        elif isinstance(value, dict):
            out.append(value)
        else:
            try:
                out.append(str(value))
            except Exception:
                out.append(None)
    return out


def build_snapshot_glob(bucket: str, date: str | None) -> str:
    """Return the S3 glob for a snapshot date (all dates when None)."""
    if date:
        return f"s3://{bucket}/snapshots/{date}/*.parquet"
    return f"s3://{bucket}/snapshots/*/*.parquet"


# ── B2 native download fallback (httpx only) ───────────────────────────


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
    resp = httpx.post(
        f"https://{os.environ.get('B2_API_URL', DEFAULT_B2_API)}/b2api/v2/b2_authorize_account",
        headers={"Authorization": f"Basic {auth}"},
        json={},
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


# ── Query runner ───────────────────────────────────────────────────────


def _connect_duckdb() -> Any:
    """Open a DuckDB connection with the httpfs extension loaded."""
    import duckdb

    con = duckdb.connect()
    # httpfs may already be bundled (1.1+); INSTALL is idempotent when online.
    with contextlib.suppress(Exception):
        con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    return con


def _sql_quote(value: str) -> str:
    """Single-quote *value* for embedding in SQL (escapes embedded quotes)."""
    return "'" + value.replace("'", "''") + "'"


def _configure_b2_secret(con: Any) -> None:
    """Create the B2 S3-compatible secret from environment credentials.

    Values come from env at runtime and are SQL-quoted via ``_sql_quote``;
    the key/secret are alphanumeric B2 credentials, never literals here.
    """
    key_id, key, _bucket, endpoint = _env_b2_credentials()
    region = endpoint.removeprefix("s3.").removesuffix(".backblazeb2.com") or "us-east-005"
    sql = (
        "CREATE SECRET b2_permanence (TYPE S3, "
        + "KEY_ID "
        + _sql_quote(key_id)
        + ", "
        + "SECRET "
        + _sql_quote(key)
        + ", "
        + "ENDPOINT "
        + _sql_quote(endpoint)
        + ", "
        + "REGION "
        + _sql_quote(region)
        + ", URL_STYLE 'path')"
    )
    con.execute(sql)


def run_snapshot_query(
    query: str,
    date: str | None = None,
    limit: int = MAX_RESULTS,
    glob_override: str | None = None,
) -> dict[str, Any]:
    """Execute read-only *query* over parquet snapshots and return JSON-safe rows.

    ``glob_override`` bypasses B2 entirely (tests / local debugging): a
    local glob such as ``/tmp/snapshots/*.parquet``. Otherwise tries
    DuckDB-direct-from-B2 first and falls back to a B2 native download.

    Returns ``{"query", "source", "columns", "rows", "row_count",
    "truncated"}``.
    """
    validate_read_only_sql(query)
    con = _connect_duckdb()
    source = "b2-direct"
    glob_path = glob_override
    tmp_holder: Any = None
    try:
        if glob_path is None:
            _key_id, _key, bucket, _endpoint = _env_b2_credentials()
            _configure_b2_secret(con)
            glob_path = build_snapshot_glob(bucket, date)
        _create_snapshots_view(con, glob_path)
        return _fetch_rows(con, query, limit, source, glob_path)
    except Exception as direct_err:
        if glob_override is not None:
            raise
        if not _looks_like_auth_error(direct_err):
            raise
        # Fall back: download via B2 native API (works with any key).
        logger.warning("b2_direct_query_failed err=%s — falling back to download", direct_err)
        tmp_holder = tempfile.TemporaryDirectory(prefix="rag-query-dl-")
        glob_path = _download_snapshots_local_into(date, tmp_holder.name)
        _create_snapshots_view(con, glob_path)
        return _fetch_rows(con, query, limit, "b2-download", glob_path)
    finally:
        if tmp_holder is not None:
            tmp_holder.cleanup()
        with contextlib.suppress(Exception):
            con.close()


def _create_snapshots_view(con: Any, glob_path: str) -> None:
    """Create the read-only ``snapshots`` VIEW over *glob_path*."""
    # glob_path is derived from env bucket/date or the caller (glob_override),
    # never from the user query; SQL-quoted via _sql_quote.
    sql = (
        "CREATE OR REPLACE VIEW snapshots AS SELECT * FROM read_parquet("  # noqa: S608
        + _sql_quote(glob_path)
        + ")"
    )
    con.execute(sql)


def _fetch_rows(con: Any, query: str, limit: int, source: str, glob_path: str) -> dict[str, Any]:
    cur = con.execute(query)
    cols = [d[0] for d in cur.description] if cur.description else []
    rows: list[list[Any]] = []
    truncated = False
    for row in cur.fetchmany(limit + 1):
        if len(rows) >= limit:
            truncated = True
            break
        rows.append(json_safe_row(row))
    return {
        "query": query,
        "source": source,
        "glob": glob_path,
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "limit": limit,
    }


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


def _looks_like_auth_error(exc: Exception) -> bool:
    """Whether an exception is an S3 auth/connect failure worth retrying."""
    text = str(exc)
    markers = (
        "InvalidAccessKeyId",
        "Authentication",
        "403",
        "Forbidden",
        "Could not resolve hostname",
        "Socket closed",
        "ConnectTimeout",
        "Connection refused",
    )
    return any(m in text for m in markers)
