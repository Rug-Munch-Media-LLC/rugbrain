"""Read-only SQL guard + JSON-safe row helpers for parquet analytics.

Split out of :mod:`rugbrain.parquet_analytics` (2026-10-25, size ratchet).
Everything here is DuckDB-connection-free: the injection boundary and the
value marshalling, so the query runner only has to compose them.
"""

from __future__ import annotations

import re
from typing import Any

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


def _sql_quote(value: str) -> str:
    """Single-quote *value* for embedding in SQL (escapes embedded quotes)."""
    return "'" + value.replace("'", "''") + "'"


def build_snapshot_glob(bucket: str, date: str | None) -> str:
    """Return the S3 glob for a snapshot date (all dates when None)."""
    if date:
        return f"s3://{bucket}/snapshots/{date}/*.parquet"
    return f"s3://{bucket}/snapshots/*/*.parquet"
