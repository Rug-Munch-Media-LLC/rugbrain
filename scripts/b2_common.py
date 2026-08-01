#!/usr/bin/env python3
"""Shared helpers for the RMI RAG B2 permanence pipeline.

Implements the on-disk formats used by ``b2_permanence.py`` (upload),
``b2_restore.py`` (download) and ``migrate_to_parquet.py`` (convert):

    snapshots/{YYYY-MM-DD}/{collection}.parquet          (default, since 2026-08)
    snapshots/{YYYY-MM-DD}/{collection}.jsonl.zst        (legacy fallback)

Per-collection file pipelines:

    Parquet (default — columnar, DuckDB-queryable from B2):
        Qdrant points -> Arrow table -> parquet (zstd level 19)

    NDJSON (legacy fallback):
        Qdrant points -> NDJSON -> zstd level 19 -> .jsonl.zst

Encryption at rest is handled by B2 server-side encryption (SSE-B2,
AES256), which is free and automatic on all buckets. There is NO
client-side encryption: files are compressed and uploaded directly.

Legacy ``*.jsonl.zst.enc`` objects (written by pre-2026-08-01 versions
that applied AES-256-GCM on top of SSE-B2) are still readable:
``decrypt_file()`` is kept as a DEPRECATED helper so existing backups
can be restored or migrated. New uploads never create them.

Environment variables (never hardcoded in this repo):
    B2_APPLICATION_KEY_ID   required
    B2_APPLICATION_KEY      required
    B2_BUCKET_NAME          default: rmi-rag-permanence
    B2_ENCRYPTION_KEY       DEPRECATED — SSE-B2 (AES256) encrypts all
                            objects at rest; kept only for reading or
                            migrating legacy .enc objects
    QDRANT_URL / QDRANT_PORT  Qdrant connection (see qdrant_store.py)
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from b2sdk.v2 import B2Api, Bucket, InMemoryAccountInfo

logger = None  # assigned by callers to avoid import-time logging config

# Legacy AES-256-GCM constants — only used by the deprecated decrypt_file()
# to read pre-2026-08-01 client-side-encrypted objects. New uploads rely on
# B2 SSE-B2 (AES256) for encryption at rest and never use these.
MAGIC = b"RMIB2AES"  # 8 bytes — must match the _HEADER ">8sHIH" magic field
ENCRYPTED_SUFFIX = ".enc"
COMPRESSED_SUFFIX = ".zst"
NDJSON_SUFFIX = ".jsonl"
PARQUET_SUFFIX = ".parquet"
DEFAULT_BUCKET = "rmi-rag-permanence"
NONCE_LEN = 12
TAG_LEN = 16
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MiB plaintext per GCM chunk (legacy format)
_HEADER = struct.Struct(">8sHIH")


# ── Environment ───────────────────────────────────────────────────────


def env_or(key: str, default: str = "") -> str:
    """Read an env var, falling back to *default*."""
    return os.environ.get(key, default)


def get_encryption_key() -> bytes:
    """DEPRECATED: return the 32-byte AES key from ``B2_ENCRYPTION_KEY``.

    B2 SSE-B2 (AES256) now encrypts objects at rest, so new uploads never
    need a client-side key. This helper is kept only for reading or
    migrating legacy ``*.enc`` objects that were written before 2026-08-01.
    """
    raw = env_or("B2_ENCRYPTION_KEY")
    if not raw:
        raise RuntimeError("B2_ENCRYPTION_KEY not set (64-char hex for AES-256-GCM)")
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise RuntimeError("B2_ENCRYPTION_KEY must be 64-char hex (32 bytes)") from exc
    if len(key) != 32:
        raise RuntimeError(f"B2_ENCRYPTION_KEY must decode to 32 bytes, got {len(key)}")
    return key


def load_env_file(path: str | None) -> None:
    """Load a systemd-style ``KEY=value`` env file without overriding
    already-set environment variables. Never logs values."""
    if not path:
        return
    env_path = Path(path)
    if not env_path.exists():
        raise FileNotFoundError(f"env file not found: {path}")
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


# ── B2 ────────────────────────────────────────────────────────────────


def get_b2_api() -> B2Api:
    """Authorize a b2sdk client from environment variables."""
    key_id = env_or("B2_APPLICATION_KEY_ID")
    key = env_or("B2_APPLICATION_KEY")
    if not key_id or not key:
        raise RuntimeError(
            "B2 credentials missing — set B2_APPLICATION_KEY_ID and "
            "B2_APPLICATION_KEY (systemd: EnvironmentFile=/srv/secrets/b2-permanence.env)"
        )
    api = B2Api(InMemoryAccountInfo())
    api.authorize_account("production", key_id, key)
    return api


def ensure_bucket(api: B2Api, bucket_name: str) -> Bucket:
    """Return the bucket, creating it (private) if it does not exist."""
    for bucket in api.list_buckets():
        if bucket.name == bucket_name:
            return bucket
    return api.create_bucket(bucket_name, "allPrivate")


def upload_file(api: B2Api, bucket_name: str, local_path: Path, key: str) -> None:
    """Upload *local_path* to ``bucket_name`` at B2 object *key*.

    b2sdk transparently switches to large-file upload above its
    threshold, so arbitrarily large snapshots are supported.
    """
    bucket = ensure_bucket(api, bucket_name)
    bucket.upload_local_file(local_file=str(local_path), file_name=key)


def list_object_keys(api: B2Api, bucket_name: str, prefix: str = "") -> list[str]:
    """Return B2 object keys under *prefix* (paged)."""
    bucket = ensure_bucket(api, bucket_name)
    keys: list[str] = []
    for version in bucket.ls(prefix, recursive=True):
        info = version[0]
        keys.append(info.file_name)
    return keys


def download_file(api: B2Api, bucket_name: str, key: str, local_path: Path) -> Path:
    """Download B2 object *key* to *local_path* (overwrites)."""
    bucket = ensure_bucket(api, bucket_name)
    with open(local_path, "wb") as f:
        bucket.download_file_by_name(key).save(f)
    return local_path


def sha256_file(path: Path) -> str:
    """SHA-256 hex digest of a file (streamed)."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# ── zstd ──────────────────────────────────────────────────────────────


def compress_file(src: Path, dst: Path, level: int = 19) -> Path:
    """Compress *src* to *dst* with zstd at *level* (streamed)."""
    import zstandard

    cctx = zstandard.ZstdCompressor(level=level)
    with open(src, "rb") as f_in, open(dst, "wb") as f_out:
        cctx.copy_stream(f_in, f_out)
    return dst


def decompress_file(src: Path, dst: Path) -> Path:
    """Decompress zstd *src* to *dst* (streamed)."""
    import zstandard

    dctx = zstandard.ZstdDecompressor()
    with open(src, "rb") as f_in, open(dst, "wb") as f_out:
        dctx.copy_stream(f_in, f_out)
    return dst


# ── Parquet ────────────────────────────────────────────────────────────
#
# Parquet snapshot schema (one file per collection per date):
#
#     id         VARCHAR   Qdrant point id (int or UUID, serialized)
#     content    VARCHAR   payload["content"] (convenience column)
#     metadata   VARCHAR   payload["metadata"] as JSON (SQL-queryable)
#     payload    VARCHAR   full Qdrant payload as JSON (exact restore)
#     embedding  DOUBLE[]  dense vector (null when named/sparse-only)
#     vectors    VARCHAR   JSON of a named/sparse vector dict, else null
#
# Keeping ``payload`` lets restore reproduce the exact original point;
# ``content``/``metadata`` make the common DuckDB queries cheap.


def parquet_schema(with_vectors: bool = True) -> Any:
    """Return the pyarrow schema used for permanence snapshots."""
    import pyarrow as pa  # type: ignore[import-untyped]

    fields: list[Any] = [
        ("id", pa.string()),
        ("content", pa.string()),
        ("metadata", pa.string()),
        ("payload", pa.string()),
        ("embedding", pa.list_(pa.float64())),
        ("vectors", pa.string()),
    ]
    return pa.schema(fields)


def point_to_row(point: Any, with_vectors: bool = True) -> dict[str, object]:
    """Convert one Qdrant scroll point into a parquet row dict.

    Handles both flat dense vectors (``embedding``) and named/sparse
    vector dicts (``vectors`` JSON). ``with_vectors=False`` writes null
    embeddings (payload-only snapshots).
    """
    pid = getattr(point, "id", None)
    payload = getattr(point, "payload", None)
    if not isinstance(payload, dict):
        payload = {}
    vector = getattr(point, "vector", None) if with_vectors else None
    row: dict[str, object] = {
        "id": str(pid) if pid is not None else "",
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
    return row


def table_from_rows(rows: Iterable[dict[str, object]]) -> Any:
    """Build a pyarrow Table from parquet row dicts."""
    import pyarrow as pa

    materialized = list(rows)
    return pa.table(
        {
            "id": [str(r.get("id") or "") for r in materialized],
            "content": [str(r.get("content") or "") for r in materialized],
            "metadata": [str(r.get("metadata") or "") for r in materialized],
            "payload": [str(r.get("payload") or "") for r in materialized],
            "embedding": [r.get("embedding") for r in materialized],
            "vectors": [r.get("vectors") for r in materialized],
        },
        schema=parquet_schema(),
    )


def write_parquet_file(dest: Path, rows: Iterable[dict[str, object]], level: int = 19) -> Path:
    """Write *rows* to *dest* as a zstd-compressed parquet file."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    pq.write_table(table_from_rows(rows), dest, compression="zstd", compression_level=level)
    return dest


def stream_parquet(dest: Path, batches: Iterable[list[dict[str, object]]], level: int = 19) -> Path:
    """Stream scroll batches into *dest* as one parquet file (bounded memory)."""
    import pyarrow.parquet as pq

    writer = pq.ParquetWriter(dest, parquet_schema(), compression="zstd", compression_level=level)
    try:
        for rows in batches:
            if rows:
                writer.write_table(table_from_rows(rows))
    finally:
        writer.close()
    return dest


def read_parquet_records(path: Path) -> list[dict[str, Any]]:
    """Read a parquet snapshot back to record dicts via DuckDB.

    Local-file reads (after a B2 download) keep restore simple and
    credential-free; direct-from-B2 queries use the same schema through
    DuckDB's httpfs extension (see src/rmi_rag/parquet_analytics.py).
    """
    import duckdb

    con = duckdb.connect()
    try:
        rel = con.table_function("read_parquet", [str(path)])
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row, strict=False)) for row in rel.fetchall()]
    finally:
        con.close()


# ── Legacy AES-256-GCM (DEPRECATED) ──────────────────────────────────
#
# New uploads never encrypt client-side: B2 SSE-B2 (AES256) encrypts
# every object at rest for free. These helpers exist only to read the
# ``*.jsonl.zst.enc`` objects written before 2026-08-01 so existing
# backups can still be restored or migrated to parquet. Do not use them
# for new data.


def decrypt_file(src: Path, dst: Path, key_hex: str) -> Path:
    """DEPRECATED: decrypt a legacy chunked AES-256-GCM *src* back to *dst*.

    Only needed for ``*.enc`` objects written before 2026-08-01. New
    snapshots are not client-side encrypted — B2 SSE-B2 (AES256) handles
    encryption at rest. Raises ``ValueError`` on any integrity violation
    (bad magic, truncated layout, or GCM auth failure).
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = bytes.fromhex(key_hex)
    if len(key) != 32:
        raise ValueError(f"expected 32-byte key, got {len(key)} bytes")
    aes = AESGCM(key)
    block_size = CHUNK_SIZE + TAG_LEN
    with open(src, "rb") as f_in, open(dst, "wb") as f_out:
        header = f_in.read(_HEADER.size)
        if len(header) != _HEADER.size:
            raise ValueError("encrypted file too short: truncated header")
        magic, version, chunk_size, _reserved = _HEADER.unpack(header)
        if magic != MAGIC:
            raise ValueError("not an RMI B2 permanence encrypted file (bad magic)")
        if version != 1:
            raise ValueError(f"unsupported encryption format version {version}")
        if chunk_size != CHUNK_SIZE:
            raise ValueError(f"chunk size mismatch: header {chunk_size} != expected {CHUNK_SIZE}")
        while True:
            nonce = f_in.read(NONCE_LEN)
            if not nonce:
                break  # clean EOF
            if len(nonce) != NONCE_LEN:
                raise ValueError("truncated nonce in encrypted file")
            ciphertext = f_in.read(block_size)
            if len(ciphertext) < TAG_LEN:
                raise ValueError("truncated ciphertext block in encrypted file")
            if len(ciphertext) < block_size:
                # Final chunk may be shorter than CHUNK_SIZE. Accept it only
                # if nothing follows (i.e. it really is the last chunk).
                trailing = f_in.read(1)
                if trailing:
                    raise ValueError("truncated ciphertext block in encrypted file")
            try:
                f_out.write(aes.decrypt(nonce, ciphertext, header))
            except Exception as exc:  # InvalidTag (auth failure)
                raise ValueError("decryption failed — wrong key or tampered file") from exc
    return dst
