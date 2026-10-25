"""Part 2/9 of scripts/b2_permanence.py."""

from __future__ import annotations

from _parts_b2_permanence._h1 import *  # noqa: F401,F403

def export_collection_parquet(
    client: qdrant_client.QdrantClient,
    name: str,
    dest: Path,
    limit: int | None = None,
    with_vectors: bool = True,
) -> int:
    """Scroll all points in *name* into a columnar parquet file at *dest*.

    Points are converted to Arrow tables per scroll batch and streamed
    through a ParquetWriter (bounded memory), then compressed with zstd
    level 6 (was 19; see ZSTD_LEVEL) inside the parquet file itself.
    """
    count_cell: list[int] = [0]

    def batches() -> Iterable[list[dict[str, object]]]:
        offset: Any = None
        remaining = limit
        while True:
            batch = min(remaining if remaining is not None else SCROLL_BATCH, SCROLL_BATCH)
            points, next_offset = client.scroll(
                collection_name=name,
                limit=batch,
                offset=offset,
                with_payload=True,
                with_vectors=with_vectors,
            )
            if points:
                rows = [b2_common.point_to_row(p, with_vectors=with_vectors) for p in points]
                count_cell[0] += len(rows)
                yield rows
            if next_offset is None:
                break
            offset = next_offset
            if len(points) == 0:
                break
            if remaining is not None:
                remaining -= len(points)
                if remaining <= 0:
                    break

    stream_parquet(dest, batches())
    return count_cell[0]


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
    offset: Any = None
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


def collection_points(client: qdrant_client.QdrantClient, name: str) -> int:
    """Live point count, or 0 when Qdrant cannot answer.

    0 means "unknown", never "empty": an unknown size must never make a
    collection look free to export.
    """
    try:
        info = client.get_collection(collection_name=name)
        return int(getattr(info, "points_count", 0) or 0)
    except Exception as exc:
        logger.warning("collection_size_unknown collection=%s err=%s", name, exc)
        return 0


def _is_oversized(client: qdrant_client.QdrantClient, name: str, shard_points: int) -> bool:
    """True only for a collection big enough to need sharding.

    Sharding every vector collection would rename every object key to
    ``<name>.part-00000.parquet`` and break readers that expect
    ``<name>.parquet`` -- two production defects to buy nothing on a
    31-point collection. So the threshold is the collection's own point count.
    """
    try:
        info = client.get_collection(collection_name=name)
        return int(getattr(info, "points_count", 0) or 0) > shard_points
    except Exception as exc:
        logger.warning("shard_size_unknown collection=%s err=%s", name, exc)
        return False


__all__ = [
    "export_collection_parquet",
    "export_collection_ndjson",
    "collection_points",
    "_is_oversized",
]
