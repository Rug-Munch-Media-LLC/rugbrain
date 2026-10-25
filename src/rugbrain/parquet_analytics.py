"""DuckDB analytics over RAG backup snapshots on Backblaze B2.

("permanence" in the module names means the B2 BACKUP job — B2 is backup/DR
for the code/RAG corpus; blockchain data is the authoritative B2 tier. See
docs/DATA-PLACEMENT.md.)

Query parquet snapshots written by ``scripts/b2_permanence.py``:

    SELECT content, metadata FROM snapshots
    WHERE json_extract_string(metadata, '$.severity') = 'critical';

Access paths: DuckDB httpfs reads parquet directly off B2's S3-compatible
endpoint (column pruning); when S3 auth fails the native-API download in
:mod:`rugbrain.parquet_b2` stages the files locally. Exposed as
``POST /api/v1/rag/query``. Credentials come from the process environment and
are never logged or returned.

Security: API-key auth in front; :func:`validate_read_only_sql` (in
:mod:`rugbrain.parquet_sql`) rejects anything but read-only statements.
"""

from __future__ import annotations

import contextlib
import logging
import tempfile
from typing import Any

from rugbrain.parquet_b2 import (
    DEFAULT_B2_API,
    DEFAULT_B2_ENDPOINT,
    DEFAULT_BUCKET,
    _download_snapshots_local_into,
    _env_b2_credentials,
)
from rugbrain.parquet_sql import (
    MAX_RESULTS,
    _sql_quote,
    build_snapshot_glob,
    json_safe_row,
    validate_read_only_sql,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_B2_API",
    "DEFAULT_B2_ENDPOINT",
    "DEFAULT_BUCKET",
    "MAX_RESULTS",
    "build_snapshot_glob",
    "json_safe_row",
    "run_snapshot_query",
    "validate_read_only_sql",
]


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


def _configure_b2_secret(con: Any) -> None:
    """Create the B2 S3-compatible secret from environment credentials.

    Values come from env at runtime and are bound as prepared-statement
    parameters (``?``) — DuckDB supports parameter binding for
    ``CREATE SECRET``, so no SQL string interpolation is involved.
    """
    key_id, key, _bucket, endpoint = _env_b2_credentials()
    region = endpoint.removeprefix("s3.").removesuffix(".backblazeb2.com") or "us-east-005"
    con.execute(
        "CREATE SECRET b2_permanence (TYPE S3, KEY_ID ?, SECRET ?, ENDPOINT ?, "
        "REGION ?, URL_STYLE 'path')",
        [key_id, key, endpoint, region],
    )


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


def _parquet_view_sql(view_name: str, parquet_arg_sql: str) -> str:
    """DDL for a read-only VIEW over one ``read_parquet(...)`` argument.

    ``view_name`` is a fixed internal identifier (snapshots / chain_events /
    chain_dex) and ``parquet_arg_sql`` MUST be a fully SQL-quoted literal
    produced by ``_sql_quote`` / ``_sql_list`` — never raw input. DuckDB does
    not accept prepared parameters in DDL statements (``CREATE VIEW ... AS
    SELECT * FROM read_parquet(?)`` fails with "can't be prepared"), so the
    file/glob argument has to be inlined as an escaped literal; quote-doubling
    is the injection boundary and the inputs are trusted env/FS paths, not
    caller SQL.
    """
    return (
        "CREATE OR REPLACE VIEW " + view_name + " AS "  # noqa: S608  # nosec B608
        "SELECT * FROM read_parquet(" + parquet_arg_sql + ")"
    )


def _create_snapshots_view(con: Any, glob_path: str) -> None:
    """Create the read-only ``snapshots`` VIEW over *glob_path*."""
    # glob_path is derived from env bucket/date or the caller (glob_override),
    # never from the user query; SQL-quoted via _sql_quote.
    con.execute(_parquet_view_sql("snapshots", _sql_quote(glob_path)))


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
