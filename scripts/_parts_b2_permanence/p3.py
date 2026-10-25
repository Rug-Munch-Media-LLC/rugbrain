"""Part 3/9 of scripts/b2_permanence.py."""

from __future__ import annotations

from _parts_b2_permanence._h1 import *  # noqa: F401,F403
from _parts_b2_permanence.p2 import _is_oversized, export_collection_ndjson, export_collection_parquet

def snapshot_collection(
    client: qdrant_client.QdrantClient,
    b2_api: B2Api,
    bucket_name: str,
    date_str: str,
    name: str,
    workdir: Path,
    limit: int | None,
    with_vectors: bool,
    fmt: str = "parquet",
    shard_points: int = 0,
    resume: dict[str, Any] | None = None,
    on_shard: Any = None,
    deadline: float | None = None,
    est_seconds: float = 0.0,
) -> dict[str, Any]:
    """Export, compress (parquet/NDJSON) and upload one collection.

    ``fmt`` is ``parquet`` (default — columnar, DuckDB-queryable from B2)
    or ``ndjson`` (legacy — zstd). No client-side encryption is applied:
    B2 SSE-B2 (AES256) encrypts every object at rest.

    ``shard_points`` > 0 splits a vector-bearing collection into multiple
    parquet objects of that many points each (see permanence_shard). One
    collection stays one manifest entry; the entry gains ``shards``.

    Returns the manifest entry for this collection.
    """
    from _parts_b2_permanence.p8 import _fmt_size
    base = workdir / name
    base.mkdir(parents=True, exist_ok=True)

    # Cross-day gate (2026-10-24): an unchanged collection keeps its previous
    # object instead of re-uploading. codebase_reference_v2 alone was ~35 GiB
    # every night, and per-date prefixes made the existing resume blind to it.
    carried = carry_if_unchanged(client, name) if fmt == "parquet" else None
    if carried is not None:
        return carried

    started = datetime.now(UTC)
    shards: list[dict[str, Any]] = []
    yielded: dict[str, bool] = {"hit": False}
    expected_shards_meta: int | None = None

    def _stop() -> bool:
        """True once this invocation's wall-clock budget is spent."""
        if deadline is None or time.monotonic() < deadline:
            return False
        yielded["hit"] = True
        return True

    # `with_vectors` is NOT part of this gate (2026-10-23). A payload-only
    # collection was exported as ONE object, and an object cannot be resumed:
    # codebase_reference_v2_8b is 3.72M points, so every firing either paid the
    # whole export or lost it on the unit timeout. Sharding it gives ~10
    # resumable parts, which is the only way a day containing it can finish.
    # SHARD WHEN IT CANNOT FINISH, not only when it is big (2026-10-23). A
    # single parquet object is not resumable, so a collection whose estimate
    # exceeds what is left of the slot has exactly two outcomes: overrun the
    # slot (losing the next timer firing, which is what a 43.3-min chunk did
    # at 15:00 today) or waste the export. Sharding turns "cannot fit" into
    # "lands one shard and resumes at its cursor next firing".
    left = (deadline - time.monotonic()) if deadline is not None else 0.0
    must_shard = bool(shard_points) and est_seconds > max(left, 0.0)
    if fmt == "parquet" and shard_points and (
        must_shard or _is_oversized(client, name, shard_points)
    ):
        key_prefix = f"{SNAPSHOT_PREFIX}/{date_str}/{name}"
        prior = resume or {}
        prior_shards = list(prior.get("shards") or [])

        def ship(new_shards: list[dict[str, Any]], points_now: int) -> None:
            """Upload every shard still on disk, then report shard progress.

            Called at each shard boundary, so the manifest names the shards
            ALREADY in B2 before the next one is written. A kill therefore
            costs one shard, not the whole collection.
            """
            for shard in new_shards:
                part = base / shard["object_key"].rsplit("/", 1)[-1]
                if not part.exists():
                    continue  # uploaded on an earlier firing
                upload_file(b2_api, bucket_name, part, shard["object_key"])
                part.unlink(missing_ok=True)
            if on_shard is not None:
                # prior_shards first: the manifest entry must describe the whole
                # collection, not just the part this firing happened to write.
                on_shard(prior_shards + list(new_shards), points_now)

        # How many shards this collection needs in total. Without it a sharded
        # entry is permanently `partial`, so a day holding one shard of the
        # elephant can never reach complete -- it stays False forever even when
        # every shard has landed.
        _expected_shards = None
        try:
            _total = int(client.count(name, exact=True).count)
            if shard_points and shard_points > 0:
                _expected_shards = max(1, -(-_total // int(shard_points)))
        except Exception:  # a count failure must not abort the export
            _expected_shards = None

        point_count, shards = export_parquet_shards(
            client,
            name,
            base,
            key_prefix,
            PARQUET_SUFFIX,
            limit=limit,
            with_vectors=with_vectors,
            shard_points=shard_points,
            level=ZSTD_LEVEL,
            start_offset=prior.get("cursor"),
            first_index=len(prior_shards),
            points_done=int(prior.get("points_done") or 0),
            on_shard=ship,
            should_stop=_stop,
        )
        # A resumed collection owns every shard, not only the ones written now:
        # dropping prior_shards here erased part-0 from the manifest entry and
        # understated bytes_stored on every resumed night.
        shards = prior_shards + list(shards)
        if not _expected_shards:
            # A count failure is not evidence that the collection shrank. The
            # denominator an earlier firing pinned is still the truth, and
            # dropping it here is how a resumed entry lost its completion test.
            _expected_shards = int(prior.get("expected_shards") or 0) or None
        if _expected_shards:
            # The manifest merge REPLACES the prior entry with this dict, so
            # recording the denominator on `prior` (the resume state, which the
            # merge discards) was a no-op: after ee5198e, sharded entries still
            # carried expected_shards=None. Carry it on the entry itself.
            expected_shards_meta = _expected_shards
        suffix = PARQUET_SUFFIX
        object_key = shards[0]["object_key"] if shards else f"{key_prefix}{suffix}"
        bytes_stored = sum(int(s.get("bytes_stored", 0)) for s in shards)
        digest = f"sha256-multipart:{len(shards)}"
    elif fmt == "parquet":
        parquet_path = base / f"{name}{PARQUET_SUFFIX}"
        point_count = export_collection_parquet(
            client, name, parquet_path, limit=limit, with_vectors=with_vectors
        )
        upload_path = parquet_path
        suffix = PARQUET_SUFFIX
        object_key = f"{SNAPSHOT_PREFIX}/{date_str}/{name}{suffix}"
        upload_file(b2_api, bucket_name, upload_path, object_key)
        bytes_stored = upload_path.stat().st_size
        digest = sha256_file(upload_path)
    else:
        ndjson_path = base / f"{name}{NDJSON_SUFFIX}"
        zst_path = base / f"{name}{NDJSON_SUFFIX}{COMPRESSED_SUFFIX}"
        point_count = export_collection_ndjson(
            client, name, ndjson_path, limit=limit, with_vectors=with_vectors
        )
        compress_file(ndjson_path, zst_path, level=ZSTD_LEVEL)
        upload_path = zst_path
        suffix = f"{NDJSON_SUFFIX}{COMPRESSED_SUFFIX}"
        object_key = f"{SNAPSHOT_PREFIX}/{date_str}/{name}{suffix}"
        upload_file(b2_api, bucket_name, upload_path, object_key)
        bytes_stored = upload_path.stat().st_size
        digest = sha256_file(upload_path)

    finished = datetime.now(UTC)
    entry: dict[str, Any] = {
        "collection": name,
        "format": fmt,
        "object_key": object_key,
        "point_count": point_count,
        "bytes_raw": bytes_stored,
        "bytes_stored": bytes_stored,
        "compression": f"zstd-{ZSTD_LEVEL}" if fmt == "ndjson" else f"parquet-zstd-{ZSTD_LEVEL}",
        "encrypted": False,  # SSE-B2 encrypts at rest; no client-side encryption
        "sha256": digest,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
    }
    if yielded["hit"]:
        # Honest shape: the shards in B2 are real, the collection is NOT done.
        # `partial` keeps entry_is_done() false; `deferred` makes the chunk
        # runner owe it next firing instead of counting it as success.
        entry["partial"] = True
        entry["deferred"] = True
    if shards:
        entry["shards"] = shards
    if expected_shards_meta:
        entry["expected_shards"] = expected_shards_meta
    if with_vectors:
        entry["vectors_included"] = True
    logger.info(
        "snapshot_ok collection=%s format=%s points=%d stored=%s key=%s shards=%d",
        name,
        fmt,
        point_count,
        _fmt_size(bytes_stored),
        object_key,
        len(shards),
    )
    if not entry.get("partial"):
        record_export(name, entry, client)
    return entry


__all__ = [
    "snapshot_collection",
]
