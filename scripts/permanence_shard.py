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
    start_offset: Any = None,
    first_index: int = 0,
    points_done: int = 0,
    on_shard: Any = None,
    should_stop: Any = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Scroll *name* into parquet shards of ~*shard_points* points each.

    Returns ``(total_points, shards)``; each shard dict is
    ``{object_key, point_count, bytes_stored, sha256, resume_cursor}``.

    SHARD-LEVEL RESUME (2026-10-23). A shard boundary is always a SCROLL BATCH
    boundary, so ``resume_cursor`` is a point id the scroll can restart from and
    replay is exact -- no duplicated point, no dropped point. Callers pass the
    last recorded cursor back as ``start_offset`` with ``first_index`` =
    len(prior shards) and ``points_done`` = their point total; part numbering
    then continues instead of restarting at 00000. Without this a kill mid-shard
    discarded the whole collection, which is why a 195.7-minute day could never
    fit an 80-minute chunk: the elephant restarted from zero every firing.
    """
    import pyarrow.parquet as pq

    base_dir.mkdir(parents=True, exist_ok=True)
    shards: list[dict[str, Any]] = []
    total = points_done
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
        index = first_index + len(shards)
        shards.append(
            {
                "object_key": f"{key_prefix}.part-{index:05d}{suffix}",
                "point_count": shard_rows,
                "bytes_stored": shard_path.stat().st_size,
                "sha256": sha256_file(shard_path),
                # The scroll position the NEXT shard starts at. None on the
                # final close (offset is None) = the collection is finished.
                "resume_cursor": offset,
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
        shard_path = base_dir / f"{name}.part-{first_index + len(shards):05d}{suffix}"
        writer = pq.ParquetWriter(
            shard_path, parquet_schema(), compression="zstd", compression_level=level
        )

    def drain() -> None:
        nonlocal buffer
        if buffer and writer is not None:
            writer.write_table(table_from_rows(buffer))
        buffer = []

    offset: Any = start_offset
    try:
        while True:
            if should_stop is not None and should_stop():
                # A batch boundary is the only safe place to yield: the shard
                # closed below starts on a scroll cursor, so the next firing
                # resumes here instead of restarting the collection. Without
                # this the elephant (rag_documents_8b, ~192 min) outlived every
                # 30-min chunk and the day could never complete.
                logger.info(
                    "shard_budget_yield collection=%s shards=%d points=%d",
                    name,
                    len(shards),
                    total,
                )
                break
            batch = min(remaining if remaining is not None else scroll_batch, scroll_batch)
            points, next_offset = client.scroll(
                collection_name=name,
                limit=batch,
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            )
            # Close at the BATCH boundary, never mid-batch: a shard that
            # starts at a batch start is resumable from a point id, a shard
            # that starts mid-batch is not. Cost is <= scroll_batch points of
            # overshoot per shard, which is the price of an exact resume.
            if writer is not None and shard_rows >= shard_points:
                drain()
                close_shard()
                if on_shard is not None:
                    on_shard(shards, total)
            for point in points:
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
        if on_shard is not None:
            on_shard(shards, total)

    return total, shards
