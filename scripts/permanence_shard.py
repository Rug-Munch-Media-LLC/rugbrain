#!/usr/bin/env python3
"""Export one oversized collection as several parquet objects.

Why: two failure modes in the 2026-10-18..23 logs are the SAME object.

  * `rag_documents_8b` (1,136,447 pts x 4096-dim) measured 114.6 min and was
    still running at ~170 min when the day's budget ran out.
  * 2026-10-18: `[Errno 28] No space left on device` while writing
    /tmp/b2-permanence-*/rag_documents_8b -- a single ~30 GB parquet staged
    before a single upload byte left the box.

One 30 GB object means one 30 GB staging file, one sha256 pass over 30 GB, one
multi-hour upload that a single 503 can invalidate end to end. Sharding bounds
all three: peak staging becomes shard-sized, a 503 costs one shard instead of
the collection, and each shard is independently resumable (the same object key
is overwritten on retry, so retries stay idempotent).

Shards are still ONE collection in the manifest: the entry keeps `object_key`
(first shard, so a naive reader restores something instead of nothing) and
gains `shards[]` with per-shard key/count/bytes/sha256.

One primary function: `export_parquet_shards`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from b2_common import parquet_schema, point_to_row, sha256_file, table_from_rows

logger = logging.getLogger("permanence_shard")

# Rows buffered before a table is written to the open shard. Bounded so the
# 4096-dim float lists never accumulate beyond one table.
ROW_BUFFER = 500


def export_parquet_shards(
    client: Any,
    name: str,
    base_dir: Path,
    key_prefix: str,
    suffix: str,
    *,
    limit: int | None = None,
    with_vectors: bool = True,
    shard_points: int = 400_000,
    scroll_batch: int = 400,
    level: int = 6,
) -> tuple[int, list[dict[str, Any]]]:
    """Scroll *name* into parquet shards of ~*shard_points* points each.

    Returns ``(total_points, shards)``; each shard dict is
    ``{object_key, point_count, bytes_stored, sha256}``.
    """
    import pyarrow.parquet as pq

    base_dir.mkdir(parents=True, exist_ok=True)
    shards: list[dict[str, Any]] = []
    total = 0
    remaining = limit

    writer: Any = None
    shard_path: Path = Path()
    shard_rows = 0
    buffer: list[dict[str, object]] = []

    def close_shard() -> None:
        nonlocal writer, shard_rows
        if writer is None:
            return
        writer.close()
        writer = None
        index = len(shards)
        shards.append(
            {
                "object_key": f"{key_prefix}.part-{index:05d}{suffix}",
                "point_count": shard_rows,
                "bytes_stored": shard_path.stat().st_size,
                "sha256": sha256_file(shard_path),
            }
        )
        logger.info(
            "shard_ok collection=%s part=%d points=%d stored=%.1fMB",
            name,
            index,
            shard_rows,
            shard_path.stat().st_size / 1024 / 1024,
        )
        # The file stays on disk: the caller uploads it and deletes it. An
        # earlier version unlinked here and the upload raised
        # InvalidUploadSource / 408 on a file that no longer existed.
        shard_rows = 0

    def open_shard() -> None:
        nonlocal writer, shard_path
        shard_path = base_dir / f"{name}.part-{len(shards):05d}{suffix}"
        writer = pq.ParquetWriter(
            shard_path, parquet_schema(), compression="zstd", compression_level=level
        )

    def drain() -> None:
        nonlocal buffer
        if buffer and writer is not None:
            writer.write_table(table_from_rows(buffer))
        buffer = []

    offset: Any = None
    try:
        while True:
            batch = min(remaining if remaining is not None else scroll_batch, scroll_batch)
            points, next_offset = client.scroll(
                collection_name=name,
                limit=batch,
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            )
            for point in points:
                if writer is not None and shard_rows >= shard_points:
                    # Drain BEFORE closing, or the last <ROW_BUFFER rows of
                    # this shard are counted in shard_rows but never written.
                    drain()
                    close_shard()
                if writer is None:
                    open_shard()
                buffer.append(point_to_row(point, with_vectors=with_vectors))
                shard_rows += 1
                total += 1
                if len(buffer) >= ROW_BUFFER:
                    drain()
            if next_offset is None or not points:
                break
            offset = next_offset
            if remaining is not None:
                remaining -= len(points)
                if remaining <= 0:
                    break
    finally:
        drain()
        close_shard()

    return total, shards
