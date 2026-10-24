#!/usr/bin/env python3
"""Nightly B2 backup sync — Qdrant snapshots to Backblaze B2.

NAMING: "permanence" in this filename means the B2 BACKUP job, not a serving
tier. The code/RAG corpus is served from the server (hot set); B2 is backup/DR
only. Blockchain data is the one tier that is authoritative on B2.
See docs/DATA-PLACEMENT.md.

Exports canonical Qdrant collections as a columnar parquet file (default;
generated ``*_8b`` mirror collections are excluded by default)
or compressed NDJSON (legacy fallback) and uploads it to B2 under
``snapshots/YYYY-MM-DD/``:

    b2://{B2_BUCKET_NAME}/snapshots/{date}/{collection}.parquet       (default)
    b2://{B2_BUCKET_NAME}/snapshots/{date}/{collection}.jsonl.zst     (legacy)

Snapshots are written with zstd level 6 (was 19 — see note) and uploaded WITHOUT
client-side encryption: B2 server-side encryption (SSE-B2, AES256)
encrypts every object at rest for free. DuckDB can query parquet
directly from B2 over the S3-compatible API (no full download). Access
control is the private bucket + a scoped application key.

A ``manifest.json`` describing every collection (format, point count,
file size, sha256, timestamps, vector metadata) is uploaded alongside
the data and a ``snapshots/latest.json`` pointer records the most
recent date.

Data lifecycle this serves:
    Hot  (Qdrant): last 90 days, actively searched
    Cold (B2):     everything, forever, nightly snapshots
    Pruning:       documents older than 90 days are moved to B2 only
                   (kept in Qdrant if high severity) — see
                   ``src/rugbrain/qdrant_store.py::prune_old_documents``

Usage:
    python scripts/b2_permanence.py --dry-run
    python scripts/b2_permanence.py --collection threat_intel_v2 --limit 10
    python scripts/b2_permanence.py --format ndjson --limit 50
    python scripts/b2_permanence.py --env-file /path/to/b2-permanence.env
    python scripts/b2_permanence.py --prune              # retention dry-run
    python scripts/b2_permanence.py --prune --prune-live # actually prune

Environment:
    B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY   (required)
    B2_BUCKET_NAME                              (default rmi-rag-permanence)
    B2_PREFER_TWIN=1 (default)                 prefer the _8b twin per collection, fall
                                            back to the base when no twin exists
    B2_CANONICAL_SUFFIX                        (default _8b)
    B2_EXCLUDE_SUFFIXES                         (legacy; only when B2_PREFER_TWIN=0)
    B2_PAYLOAD_ONLY_COLLECTIONS                 (default codebase_reference_v2; large vector corpus)
    B2_ENCRYPTION_KEY                           DEPRECATED — SSE-B2 encrypts
                                                at rest; kept only for
                                                reading/migrating legacy .enc
    QDRANT_URL / QDRANT_PORT                    Qdrant connection

See scripts/b2_common.py for the file format. Restore:
scripts/b2_restore.py. Convert legacy snapshots: scripts/migrate_to_parquet.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import qdrant_client
import qdrant_client.http.exceptions
from b2sdk.v2 import B2Api

sys.path.insert(0, str(Path(__file__).resolve().parent))

import b2_common
from b2_common import (
    COMPRESSED_SUFFIX,
    NDJSON_SUFFIX,
    PARQUET_SUFFIX,
    compress_file,
    download_file,
    ensure_bucket,
    env_or,
    get_b2_api,
    load_env_file,
    sha256_file,
    stream_parquet,
    upload_file,
)
from permanence_chunk_plan import estimate_minutes, plan_chunks
from permanence_chunk_run import run_chunk
from permanence_shard import export_parquet_shards
from permanence_scope import daily_scope
from permanence_shard_contract import pin_expected_shards
from permanence_shard_resume import entry_is_done, resume_state

logger = logging.getLogger("b2_permanence")

# Qdrant fallback chain (mirrors src/rugbrain/qdrant_store.py)
# 2026-08-29: was http://rmi-qdrant-v2:6333 — this script runs on the HOST via
# systemd (not inside the Docker network), so the container alias never
# resolved and the fallback chain landed on 127.0.0.1:6333, the decommissioned
# legacy v1 instance (empty). Production rmi-qdrant-v2 publishes 6333->6334.
DEFAULT_QDRANT_HOST = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
DEFAULT_QDRANT_PORT = os.getenv("QDRANT_PORT", "6334")

MANIFEST_NAME = "manifest.json"
LATEST_NAME = "latest.json"
DEFAULT_EXCLUDED_SUFFIXES = ("_8b",)
DEFAULT_PAYLOAD_ONLY_COLLECTIONS = ("codebase_reference_v2",)
# 2026-09-18: was hardcoded level 19 (max). With 41 collections that run exceeded the
# 4h TimeoutStartSec and the unit was SIGTERM'd (ExecMainStatus=15) — every night.
# Level 19 is ~5-10x slower than level 6 for a marginal size win, and B2 storage is
# cheap. Override with CC_ZSTD_LEVEL if a smaller archive is ever needed.
ZSTD_LEVEL = int(os.environ.get("CC_ZSTD_LEVEL", "6"))

SNAPSHOT_PREFIX = "snapshots"

# The smallest unit of work worth starting. Below this the estimate is noise
# and the item is cheap enough to attempt anyway.
MIN_ESTIMATE_S = 20.0
# 2026-10-15: was 1000. The nightly run OOM-killed at 4G with 1000-row
# Arrow tables across ~60 collections; 400 keeps per-batch tables small
# (paired with ARROW_DEFAULT_MEMORY_POOL=system in 62-arrow-pool.conf).
SCROLL_BATCH = 400
MANIFEST_SCHEMA_VERSION = 2


# ── Qdrant helpers ────────────────────────────────────────────────────


def resolve_qdrant_client(host: str | None, port: int | None) -> qdrant_client.QdrantClient:
    """Build a QdrantClient, trying the fallback chain until one answers.

    Chain: explicit --qdrant-url → QDRANT_URL → 127.0.0.1:port →
    localhost:port. Returns a client for the first reachable URL.
    """
    port_str = str(port or DEFAULT_QDRANT_PORT)
    candidates: list[str] = []
    if host:
        candidates.append(host)
    candidates.extend(
        [
            DEFAULT_QDRANT_HOST,
            f"http://127.0.0.1:{port_str}",
            f"http://localhost:{port_str}",
        ]
    )

    seen: set[str] = set()
    errors: list[str] = []
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            client = qdrant_client.QdrantClient(url=url, timeout=30)
            _ = client.get_collections()
            logger.info("qdrant_connected url=%s", url)
            return client
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            logger.debug("qdrant_probe_failed url=%s err=%s", url, exc)

    raise RuntimeError("Cannot reach Qdrant — tried " + "; ".join(errors))


def list_collections(client: qdrant_client.QdrantClient) -> list[str]:
    """Return collection names sorted alphabetically."""
    return sorted(c.name for c in client.get_collections().collections)


def filter_collections(collections: list[str], excluded_suffixes: tuple[str, ...]) -> list[str]:
    """Exclude generated mirror collections from the canonical snapshot set."""
    suffixes = tuple(suffix for suffix in excluded_suffixes if suffix)
    if not suffixes:
        return list(collections)
    return [name for name in collections if not name.endswith(suffixes)]


def resolve_payload_only_collections(explicit: list[str] | None) -> set[str]:
    """Resolve collections whose vectors are too large for the nightly window.

    LINEAGE-AWARE (2026-09-28). This used to return the configured names
    verbatim, and that became a landmine the moment the writer started shipping
    `_8b` twins instead of base collections.

    B2_PAYLOAD_ONLY_COLLECTIONS names `codebase_reference_v2`. The writer now
    snapshots `codebase_reference_v2_8b` for that lineage, and
    collection_has_vectors() tests membership by exact name — so the twin was
    NOT payload-only. with_vectors defaults to True and the unit passes no
    arguments, so the canonical collection would have exported its VECTORS:
    2,126,630 points x 4096 floats, roughly 35 GB for one collection, in a job
    capped at 4G RAM and a 45-minute window.

    A payload-only decision is a property of the CONTENT, not of the collection
    name — the twin holds the same chunks with a different embedding, so if the
    base is payload-only the twin must be too. Every configured name therefore
    expands to itself plus its canonical suffix variant, and any live collection
    that either maps onto a configured name is covered regardless of which
    generation is currently being shipped.
    """
    raw = (
        explicit
        if explicit is not None
        else os.getenv(
            "B2_PAYLOAD_ONLY_COLLECTIONS", ",".join(DEFAULT_PAYLOAD_ONLY_COLLECTIONS)
        ).split(",")
    )
    configured = {part.strip() for part in raw if part.strip()}
    suffix = os.getenv("B2_CANONICAL_SUFFIX", "_8b")

    # A name is payload-only if it, its twin, or its base is configured. This is
    # deliberately symmetric so the set is correct whichever generation the
    # selection logic picks on a given day.
    expanded = set(configured)
    for name in configured:
        if name.endswith(suffix):
            expanded.add(name[: -len(suffix)])
        else:
            expanded.add(name + suffix)
    return expanded


def collection_has_vectors(name: str, requested: bool, payload_only: set[str]) -> bool:
    """Return whether this collection should export vector data."""
    return requested and name not in payload_only


def collection_info(client: qdrant_client.QdrantClient, name: str) -> dict[str, Any]:
    """Return vector size/distance metadata for the manifest."""
    try:
        info = client.get_collection(collection_name=name)
        vectors = info.config.params.vectors
        size: object = getattr(vectors, "size", None)
        distance: object = getattr(vectors, "distance", None)
        return {"vector_size": size, "distance": str(distance) if distance else None}
    except Exception as exc:
        logger.warning("collection_info_failed collection=%s err=%s", name, exc)
        return {"vector_size": None, "distance": None}


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


# ── Snapshot pipeline ─────────────────────────────────────────────────


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
    base = workdir / name
    base.mkdir(parents=True, exist_ok=True)

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
    return entry


def _merge_shard_progress(
    prior: dict[str, Any] | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    """Never lose shards already in B2; derive `partial` from the arithmetic.

    MEASURED DEFECT (2026-10-24, three consecutive firings): rag_documents
    landed 8 shards at 03:00, 10 at 03:40 and 3 at 04:20 -- each run restarted
    at part-00000 and the manifest entry was REPLACED by the newer, smaller
    shard list. The day's count therefore never advanced, every firing
    re-exported ~10 x 611 MB, and the collection could never complete.

    The rule is monotonic by construction:
      * shards are unioned by object_key (a re-export of the same part cannot
        shrink the list, and a genuinely new part is added);
      * expected_shards is the max of both sides (never lowered);
      * `partial` is derived from len(shards) vs expected_shards, so a caller
        that forgets the flag can no longer advertise a half-exported
        collection as done.
    """
    if not incoming:
        return incoming
    prior_shards = list((prior or {}).get("shards") or [])
    incoming_shards = list(incoming.get("shards") or [])
    if not prior_shards and not incoming_shards:
        return incoming
    merged: dict[str, dict[str, Any]] = {}
    for shard in prior_shards + incoming_shards:
        key = shard.get("object_key") if isinstance(shard, dict) else None
        if key:
            merged[str(key)] = shard
    expected = max(
        int((prior or {}).get("expected_shards") or 0),
        int(incoming.get("expected_shards") or 0),
    )
    out = dict(incoming)
    out["shards"] = [merged[k] for k in sorted(merged)]
    out["point_count"] = sum(int(s.get("point_count", 0) or 0) for s in out["shards"])
    out["bytes_stored"] = sum(int(s.get("bytes_stored", 0) or 0) for s in out["shards"])
    if expected:
        out["expected_shards"] = expected
    if expected and len(out["shards"]) >= expected:
        out.pop("partial", None)
        out.pop("deferred", None)
    else:
        out["partial"] = True
    return out


def upload_manifest(
    b2_api: B2Api,
    bucket_name: str,
    date_str: str,
    entries: list[dict[str, Any]],
    expected_count: int | None = None,
    missing: dict[str, str] | None = None,
    exclude_from_daily: bool = True,
    drop: set[str] | None = None,
) -> None:
    """Upload manifest.json for the day plus the latest.json pointer.

    MERGES, never replaces (2026-09-28). This used to overwrite the day's
    manifest with whatever the current run happened to snapshot, which is a
    data-integrity bug rather than a cosmetic one.

    Observed live, caused by a `--collection hacks_v2_8b` proof run against a
    day that already held a full 54-collection snapshot: the parquet objects
    were all still in B2 (55 of them), but the manifest was replaced with a
    single-entry document. Anyone restoring from that manifest would restore
    ONE collection and believe the backup was complete — a silently truncated
    restore is worse than a failed one, because it does not announce itself.

    The failure is invisible in the object listing: the files are all there.
    Only the manifest lies, and the manifest is what restore trusts.

    So: if the day already has a manifest, read it, index its entries by
    collection, and overlay this run's entries on top. A re-snapshot of a
    collection replaces its own entry (the newer sha256/point count is the
    truth); a collection absent from this run keeps its previous entry. That
    makes partial and repeated runs composable instead of destructive.

    PARTIAL vs COMPLETE (2026-10-23). Merging made a day look finished when it
    was not: a manifest with 77 of 85 collections verified as "usable" because
    the only completeness test was a static `>= 40` floor. The manifest now
    carries the truth of the run that produced it --
    ``expected_collections``, ``complete`` and ``missing[]`` -- so a reader can
    distinguish "85 of 85" from "77 of 85, 8 still owed" without guessing.
    ``missing`` is merged like ``collections``: a collection that later succeeds
    is dropped from ``missing`` rather than left to contradict its own entry.
    """
    manifest_key = f"{SNAPSHOT_PREFIX}/{date_str}/{MANIFEST_NAME}"
    by_collection: dict[str, dict[str, Any]] = {}
    prior_missing: dict[str, dict[str, Any]] = {}
    prior_expected: int | None = None
    prior_count = 0
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as pt:
            prior_path = Path(pt.name)
        try:
            # download_file raises when the object does not exist — the normal
            # case on a first run of the day, not an error condition.
            download_file(b2_api, bucket_name, manifest_key, prior_path)
            prior = json.loads(prior_path.read_text(encoding="utf-8"))
        finally:
            prior_path.unlink(missing_ok=True)
        if isinstance(prior, dict):
            prior_entries = prior.get("collections")
            if isinstance(prior_entries, list):
                prior_count = len(prior_entries)
                for e in prior_entries:
                    if isinstance(e, dict) and e.get("collection"):
                        by_collection[str(e["collection"])] = e
            if isinstance(prior.get("missing"), list):
                for m in prior["missing"]:
                    if isinstance(m, dict) and m.get("collection"):
                        prior_missing[str(m["collection"])] = m
            if isinstance(prior.get("expected_collections"), int):
                prior_expected = prior["expected_collections"]
    except Exception as exc:  # a missing/unreadable prior manifest is fine
        logger.info("manifest_merge no_prior_manifest key=%s (%s)", manifest_key, exc)

    for name in drop or ():
        by_collection.pop(str(name), None)
        prior_missing.pop(str(name), None)
    for e in entries:
        name = e.get("collection")
        if not name:
            continue
        e = _merge_shard_progress(by_collection.get(str(name)), e)
        by_collection[str(name)] = e

    merged = [by_collection[k] for k in sorted(by_collection)]
    if prior_count:
        logger.info(
            "manifest_merge date=%s prior=%d incoming=%d merged=%d "
            "(partial runs no longer truncate the day)",
            date_str,
            prior_count,
            len(entries),
            len(merged),
        )

    missing_by: dict[str, dict[str, Any]] = dict(prior_missing)
    for name, error in (missing or {}).items():
        missing_by[name] = {
            "collection": name,
            "error": error,
            "at": datetime.now(UTC).isoformat(),
        }
    # A collection that landed IN THIS RUN is not missing, whatever an earlier
    # flush said. Prior-day entries must not clear it: a deferral would erase
    # its own evidence and the day would look complete while owing work.
    for entry in entries:
        if entry.get("collection"):
            missing_by.pop(str(entry["collection"]), None)

    if exclude_from_daily:
        # "Out of daily scope" has to mean the day stops owing it, otherwise the
        # prior entry and its `missing[]` row keep a 97-of-98 day incomplete
        # forever. A --collection run passes exclude_from_daily=False, so the
        # weekly contract can still complete the very collection the daily run
        # drops.
        out = set(daily_scope([])[1])
        if out:
            # Keep any entry already in the day (the weekly run's shards are
            # real data and must not be erased by the nightly), but stop the
            # day OWING it: drop its missing[] row and do not let it inflate
            # the denominator below.
            cleared = [n for n in out if n in missing_by]
            for n in out:
                missing_by.pop(n, None)
            if cleared:
                logger.warning(
                    "daily_scope_cleared_missing date=%s collections=%s",
                    date_str,
                    ",".join(sorted(cleared)),
                )

    expected = expected_count if expected_count is not None else prior_expected
    if expected is None:
        expected = len(merged)
    # Never let a narrow run (--collection, a debug chunk) LOWER the day's
    # denominator: that is how a 4-of-85 manifest would advertise itself as
    # complete. Only a larger truth may raise it.
    # The denominator counts DAILY collections: an out-of-scope entry that
    # happens to be present (weekly elephant) must not raise what the day owes.
    # The excluded set is a property of the COLLECTION, not of the flag this
    # caller passed. A --collection (weekly elephant) run used to compute
    # out_present=0, so on a completed 85-entry day its extra entry set
    # expected=max(85, 86)=86 and the day could never be complete again. The
    # elephant fires on Sunday morning precisely AFTER the nightly has written
    # that day, so the shipped weekly contract would have corrupted every
    # Sunday. Same subtraction on both paths.
    scope_out = set(daily_scope([])[1])
    out_present = sum(1 for e in merged if e.get("collection") in scope_out)
    expected = max(int(expected), len(merged) - out_present)
    missing_list = [missing_by[k] for k in sorted(missing_by)]
    # A half-written collection (partial: true) is a manifest ENTRY but not a
    # landed collection. Counting it would let a day holding one shard of the
    # elephant advertise 85/85 -- the same class of lie as the 4-of-85 stamp.
    # 2026-10-24 -- THE SAME LIE, ONE LAYER DOWN: `landed` was counted over
    # EVERY entry while `expected` had already subtracted the out-of-scope
    # weekly elephant. With rag_documents partial and rag_documents_8b landed,
    # 96 landed daily + 1 landed elephant summed to 97 against expected 97 and
    # the day advertised complete=True. Landed and expected must be counted in
    # the SAME universe, and a partial is never landed.
    landed = sum(
        1
        for e in merged
        if not e.get("partial") and e.get("collection") not in scope_out
    )
    # A deferred out-of-scope collection is still recorded in missing[], but it
    # cannot gate the DAILY contract: the elephant owes weeks, not a day.
    daily_missing = [m for m in missing_list if m.get("collection") not in scope_out]
    complete = landed >= expected and not daily_missing

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "date": date_str,
        "encryption": {
            "algorithm": "SSE-B2",
            "key_env": None,
            "enabled": True,
            "note": "B2 server-side encryption (AES256, SSE-B2) encrypts all objects "
            "at rest for free. No client-side encryption is applied; client-side "
            "AES-256-GCM was removed 2026-08-01.",
        },
        "formats": sorted({str(e.get("format", "ndjson")) for e in merged}),
        "collections": merged,
        "total_points": sum(int(e.get("point_count", 0)) for e in merged),
        "expected_collections": expected,
        "complete": complete,
        "missing": missing_list,
        # WHAT THIS DAY IS NOT SUPPOSED TO HOLD. Without it a reader comparing
        # against the live Qdrant collection list counts one collection too
        # many forever, and "97 of 97 by design" is indistinguishable from
        # "97 of 98, one missing".
        "daily_scope": {"excluded_from_daily": daily_scope([])[1]},
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(manifest, tmp, indent=2)
        tmp_path = Path(tmp.name)
    try:
        upload_file(b2_api, bucket_name, tmp_path, f"{SNAPSHOT_PREFIX}/{date_str}/{MANIFEST_NAME}")
        # latest.json is the pointer restore resolves FIRST, so it must never
        # name a day that is still owing collections. From 2026-10-23 the
        # manifest is flushed after every collection; advancing the pointer on
        # each flush would advertise a partial day as the newest good one. So
        # the pointer only moves when the day is complete -- and if the day
        # never completes, the pointer stays on the last complete day and the
        # staleness alert fires on B2 truth.
        if complete:
            latest = {
                "date": date_str,
                "manifest": f"{SNAPSHOT_PREFIX}/{date_str}/{MANIFEST_NAME}",
            }
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(latest, f, indent=2)
            upload_file(b2_api, bucket_name, tmp_path, f"{SNAPSHOT_PREFIX}/{LATEST_NAME}")
        else:
            logger.warning(
                "manifest_partial date=%s have=%d expected=%d missing=%d "
                "(latest.json NOT advanced)",
                date_str,
                landed,
                expected,
                # The GAP, not just the collections this firing named: the log
                # said missing=1 on a day that owed 21 (2026-10-23), which is
                # exactly the number an operator reads first.
                max(len(missing_list), max(expected - landed, 0)),
            )
    finally:
        tmp_path.unlink(missing_ok=True)
    logger.info(
        "manifest_uploaded date=%s collections=%d expected=%d complete=%s",
        date_str,
        landed,
        expected,
        complete,
    )


def todays_done_collections(b2_api: B2Api, bucket_name: str, date_str: str) -> set[str]:
    """Collections already recorded in today's manifest — the resume cursor.

    Absence of a manifest is not an error (it is the normal first run of a
    day), so any read failure yields the empty set and the chunk re-exports
    from the start. Re-exporting is safe: it overwrites the same object keys.
    """
    # A partial entry is NOT done: its shards are in B2 but it still owes the
    # rest of the collection, so it stays in the run and resumes from its own
    # recorded cursor (see permanence_shard_resume).
    return {
        name
        for name, entry in todays_manifest_entries(b2_api, bucket_name, date_str).items()
        if entry_is_done(entry)
    }


def todays_manifest_entries(
    b2_api: B2Api, bucket_name: str, date_str: str
) -> dict[str, dict[str, Any]]:
    """Every entry in today's manifest by collection — complete AND partial."""
    manifest_key = f"{SNAPSHOT_PREFIX}/{date_str}/{MANIFEST_NAME}"
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as pt:
            path = Path(pt.name)
        try:
            download_file(b2_api, bucket_name, manifest_key, path)
            prior = json.loads(path.read_text(encoding="utf-8"))
        finally:
            path.unlink(missing_ok=True)
    except Exception:
        return {}
    if not isinstance(prior, dict):
        return {}
    return {
        str(entry["collection"]): entry
        for entry in prior.get("collections") or []
        if isinstance(entry, dict) and entry.get("collection")
    }


# ── Retention / prune ──────────────────────────────────────────────
#
# Policy (BACKUP-POLICY.md, RAG Permanence):
#   keep 30 daily / 4 weekly / 12 monthly snapshots.
#   A snapshot is a dated directory: snapshots/YYYY-MM-DD/{...}.
#   Daily   = newest 30 dated snapshot dirs
#   Weekly  = newest snapshot dir in each of the last 4 ISO weeks
#   Monthly = newest snapshot dir in each of the last 12 calendar months
# Deletion is DATED-DIR-GRANULAR and scoped to the snapshots/ prefix.
# latest.json / manifest.json pointers are never deleted.

SNAPSHOT_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
# Retention counts — sane equivalent of the documented 30/4/12
RETAIN_DAILY = 30
RETAIN_WEEKLY = 4
RETAIN_MONTHLY = 12
# Safety floor: never prune below this many retained snapshots.
# Prevents a misconfig from deleting everything (e.g. empty bucket edge).
MIN_RETAIN_FLOOR = 1


def parse_snapshot_date(key: str) -> str | None:
    """Extract the ``YYYY-MM-DD`` snapshot dir from an object key.

    Only keys under ``snapshots/YYYY-MM-DD/`` (with a REAL calendar date)
    qualify; everything else (latest.json, manifest.json at the prefix
    root, stray objects, malformed dates) returns None so the prune pass
    can never touch it.
    """
    parts = key.split("/")
    if len(parts) >= 2 and parts[0] == SNAPSHOT_PREFIX:
        candidate = parts[1]
        if SNAPSHOT_DATE_RE.match(candidate):
            # Reject syntactically-shaped but invalid dates (e.g. 2026-13-99)
            try:
                datetime.strptime(candidate, "%Y-%m-%d")
            except ValueError:
                return None
            return candidate
    return None


def snapshot_dates_on_remote(b2_api: B2Api, bucket_name: str) -> list[str]:
    """Return the sorted list of dated snapshot dirs present on B2."""
    bucket = ensure_bucket(b2_api, bucket_name)
    dates: set[str] = set()
    # b2sdk ls with recursive=True returns (version_info, folder_name) tuples.
    for version, _folder in bucket.ls(f"{SNAPSHOT_PREFIX}/", recursive=True):
        date = parse_snapshot_date(version.file_name)
        if date:
            dates.add(date)
    return sorted(dates)


def retention_keep_set(
    dates: list[str],
    keep_daily: int = RETAIN_DAILY,
    keep_weekly: int = RETAIN_WEEKLY,
    keep_monthly: int = RETAIN_MONTHLY,
) -> set[str]:
    """Compute the set of snapshot dates to KEEP under the 30/4/12 policy.

    ``dates`` must be sorted ascending. Returns the union of:
      - the newest ``keep_daily`` dates (daily cadence)
      - the newest snapshot date within each of the last ``keep_weekly``
        ISO weeks (weekly cadence)
      - the newest snapshot date within each of the last ``keep_monthly``
        calendar months (monthly cadence)
    """
    keep: set[str] = set()
    if not dates:
        return keep

    # Daily — newest N dates
    keep.update(dates[-keep_daily:])

    # Weekly — newest date per ISO (year, week)
    by_week: dict[tuple[int, int], str] = {}
    for d in dates:
        dt = datetime.strptime(d, "%Y-%m-%d")
        iso = dt.isocalendar()
        by_week[(iso[0], iso[1])] = d  # ascending iter => last wins = newest
    for _week, date in list(sorted(by_week.items()))[-keep_weekly:]:
        keep.add(date)

    # Monthly — newest date per (year, month)
    by_month: dict[tuple[int, int], str] = {}
    for d in dates:
        dt = datetime.strptime(d, "%Y-%m-%d")
        by_month[(dt.year, dt.month)] = d  # ascending iter => last wins = newest
    for _month, date in list(sorted(by_month.items()))[-keep_monthly:]:
        keep.add(date)

    return keep


def prune_snapshots(
    b2_api: B2Api,
    bucket_name: str,
    dry_run: bool = True,
    keep_daily: int = RETAIN_DAILY,
    keep_weekly: int = RETAIN_WEEKLY,
    keep_monthly: int = RETAIN_MONTHLY,
) -> dict[str, Any]:
    """Enforce snapshot retention on the remote (30 daily / 4 weekly / 12 monthly).

    Deletes whole dated snapshot dirs that are NOT in the keep set. Deletion
    is scoped strictly to ``snapshots/YYYY-MM-DD/`` — pointers and stray
    objects are never touched.

    SAFETY GUARDS:
      * ``dry_run=True`` (default) only logs what would be deleted.
      * Refuses the permanent/object-lock bucket (``rugmunch-permanent``).
      * Refuses to prune when the candidate set would leave fewer than
        ``MIN_RETAIN_FLOOR`` snapshots (protects against wiping everything).
      * Never deletes the newest snapshot date.
      * No-op when there is nothing to prune.

    Returns a summary dict.
    """
    result: dict[str, Any] = {"bucket": bucket_name, "dry_run": dry_run}

    # SAFETY: never prune the object-lock/permanent bucket
    if "permanent" in bucket_name.lower() or bucket_name in {
        "rugmunch-permanent",
        "rmi-backup-encrypted",
    }:
        logger.error(
            "prune_refused bucket=%s permanent/object-lock bucket — refusing to prune",
            bucket_name,
        )
        result["refused"] = "permanent-bucket"
        return result

    dates = snapshot_dates_on_remote(b2_api, bucket_name)
    result["snapshot_dates"] = len(dates)
    if not dates:
        logger.info("prune: no dated snapshots found on %s — nothing to do", bucket_name)
        return result

    keep = retention_keep_set(dates, keep_daily, keep_weekly, keep_monthly)

    # SAFETY: floor guard — if the raw retention policy would retain
    # NOTHING (empty keep set), refuse before any deletion. This is the
    # "would delete everything" protection: a misconfigured/zeroed policy
    # must never translate into wiping the remote.
    if len(keep) == 0:
        logger.error(
            "prune_refused: retention policy retained 0 of %d snapshots on %s — aborting",
            len(dates),
            bucket_name,
        )
        result["refused"] = "floor-guard"
        return result

    # SAFETY: never delete the newest snapshot (belt & braces — it is in keep)
    keep.add(dates[-1])

    to_delete = [d for d in dates if d not in keep]
    result["keep"] = len(keep)
    result["would_delete"] = len(to_delete)

    if not to_delete:
        logger.info(
            "prune: nothing to prune (snapshots=%d kept=%d)",
            len(dates),
            len(keep),
        )
        return result

    # SAFETY: floor guard — never leave fewer than MIN_RETAIN_FLOOR snapshots.
    # Defensive: len(to_delete) >= len(dates) is impossible after the
    # newest-date keep above, but is checked anyway as belt-and-braces.
    if len(to_delete) >= len(dates):
        logger.error(
            "prune_refused: would delete %d/%d snapshots on %s — aborting",
            len(to_delete),
            len(dates),
            bucket_name,
        )
        result["refused"] = "floor-guard"
        return result
    retained_after = len(dates) - len(to_delete)
    if retained_after < MIN_RETAIN_FLOOR:
        logger.error(
            "prune_refused: would leave %d snapshots (floor %d) on %s — aborting",
            retained_after,
            MIN_RETAIN_FLOOR,
            bucket_name,
        )
        result["refused"] = "floor-guard"
        return result

    logger.info(
        "prune: snapshots=%d keep=%d delete=%d (dry_run=%s)",
        len(dates),
        len(keep),
        len(to_delete),
        dry_run,
    )

    bucket = ensure_bucket(b2_api, bucket_name)
    deleted_dirs: list[str] = []
    deleted_objects = 0
    for date in to_delete:
        prefix = f"{SNAPSHOT_PREFIX}/{date}/"
        version_infos = [
            version
            for version, _folder in bucket.ls(prefix, recursive=True)
            if parse_snapshot_date(version.file_name)
        ]
        if dry_run:
            logger.info(
                "prune_dry_run: would delete snapshots/%s/ (%d objects)",
                date,
                len(version_infos),
            )
            continue
        # Live delete — file_id + file_name required by b2sdk v2
        for version in version_infos:
            bucket.delete_file_version(version.id_, version.file_name)
            deleted_objects += 1
        deleted_dirs.append(date)
        logger.info("prune_deleted date=%s objects=%d", date, len(version_infos))

    result["deleted_dirs"] = deleted_dirs
    result["deleted_objects"] = deleted_objects
    return result


# ── CLI ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Nightly Qdrant → B2 permanence sync (parquet by default, "
        "compressed NDJSON fallback). B2 SSE-B2 encrypts objects at rest.",
    )
    p.add_argument("--dry-run", action="store_true", help="Do everything except upload")
    p.add_argument(
        "--format",
        choices=["parquet", "ndjson"],
        default="parquet",
        help="Snapshot format: parquet (default, DuckDB-queryable) or ndjson (legacy)",
    )
    p.add_argument(
        "--exclude-suffix",
        action="append",
        default=None,
        help="Exclude collection names ending with this suffix; repeatable (default: _8b)",
    )
    p.add_argument(
        "--payload-only-collection",
        action="append",
        default=None,
        help="Export payload without vectors for this collection; repeatable",
    )
    p.add_argument(
        "--collection",
        action="append",
        default=[],
        help="Restrict to a collection (repeatable; default: all)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max points to export per collection (testing)",
    )
    p.add_argument(
        "--chunk-count",
        type=int,
        default=None,
        help="Split the day into this many time-bounded chunks (see permanence_chunk_plan)",
    )
    p.add_argument(
        "--chunk-index",
        type=int,
        default=None,
        help="Run only this chunk (0-based). Requires --chunk-count",
    )
    p.add_argument(
        "--auto-chunk",
        action="store_true",
        help="With --chunk-count: run the first chunk still owing collections "
        "for today, so repeated timer firings complete the day",
    )
    p.add_argument(
        "--max-seconds",
        type=int,
        default=4800,
        help="Wall-clock budget for this invocation (default 4800 = 80min, "
        "deliberately below the unit's 90min timeout so it exits cleanly)",
    )
    p.add_argument(
        "--slot-seconds",
        type=int,
        default=int(os.environ.get("B2_SLOT_SECONDS", "2100")),
        help="HARD wall clock for starting new work (default 2100 = 35min, "
        "5min inside the 40min timer slot so a chunk can never take the next "
        "slot down with it). Distinct from --max-seconds, which is when an "
        "in-flight collection stops scrolling and lands what it has",
    )
    p.add_argument(
        "--carry-days",
        type=int,
        default=int(os.environ.get("B2_CARRY_DAYS", "0")),
        help="How many previous dated manifests to carry COMPLETED collections "
        "forward from (default 0 = OFF). See PARTIAL-DAY CONTRACT: a dated "
        "snapshot must be self-contained, so inheriting yesterday's objects "
        "silently raises the RPO. Enable only to trade RPO for transfer",
    )
    p.add_argument(
        "--date",
        default=None,
        help="Finish a SPECIFIC open day (YYYY-MM-DD) instead of today. An "
        "operator tool for a day left partial; the timer never passes it",
    )
    p.add_argument(
        "--no-carry",
        action="store_true",
        help="Do not inherit completed collections from previous dates",
    )
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="Re-export collections already present in today's manifest "
        "(default: skip them — resumption is idempotent)",
    )
    p.add_argument(
        "--shard-points",
        type=int,
        default=400_000,
        help="Split a vector collection larger than this many points into "
        "multiple parquet objects (0 disables sharding)",
    )
    p.add_argument(
        "--no-vectors", action="store_true", help="Skip vectors in export (payload only)"
    )
    p.add_argument("--qdrant-url", default=None, help="Qdrant URL override")
    p.add_argument("--qdrant-port", type=int, default=None, help="Qdrant port override")
    p.add_argument("--b2-bucket", default=None, help="B2 bucket override")
    p.add_argument(
        "--env-file",
        default=None,
        help="Load KEY=value env file (e.g. /path/to/b2-permanence.env)",
    )
    p.add_argument(
        "--prune",
        action="store_true",
        help="Enable remote retention pruning (keep 30 daily / 4 weekly / 12 "
        "monthly). Dry-run by default — pass --prune-live (and NOT --dry-run) "
        "to actually delete.",
    )
    p.add_argument(
        "--prune-live",
        action="store_true",
        help="With --prune: actually delete expired snapshot dirs instead of "
        "dry-run. Ignored if --dry-run is set. Requires --prune.",
    )
    p.add_argument(
        "--keep-daily",
        type=int,
        default=RETAIN_DAILY,
        help=f"Daily snapshots to keep (default {RETAIN_DAILY})",
    )
    p.add_argument(
        "--keep-weekly",
        type=int,
        default=RETAIN_WEEKLY,
        help=f"Weekly snapshots to keep (default {RETAIN_WEEKLY})",
    )
    p.add_argument(
        "--keep-monthly",
        type=int,
        default=RETAIN_MONTHLY,
        help=f"Monthly snapshots to keep (default {RETAIN_MONTHLY})",
    )
    p.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    return p


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n //= 1024
    return f"{n:.1f}TB"


def canonical_denominator(names: list[str], suffix: str = "_8b") -> int:
    """How many collections a normal day backs up, by the prefer-twin rule.

    Mirrors the selection below: a `_8b` collection is canonical; a base
    collection is canonical only when it has no `_8b` twin. Used ONLY as the
    manifest's `expected_collections` denominator, so the number a partial-day
    alert compares against is the size of a full day rather than the size of
    whatever subset this particular invocation happened to be asked for.
    """
    known = set(names)
    return sum(1 for n in names if n.endswith(suffix) or (n + suffix) not in known)


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logging.getLogger("b2sdk").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        load_env_file(args.env_file)
        client = resolve_qdrant_client(args.qdrant_url, args.qdrant_port)
    except Exception as exc:
        logger.critical("startup_failed: %s", exc)
        return 2

    bucket_name = args.b2_bucket or env_or("B2_BUCKET_NAME", b2_common.DEFAULT_BUCKET)
    fmt = args.format

    # B2_ENCRYPTION_KEY is deprecated: SSE-B2 (AES256) encrypts objects at
    # rest, so client-side encryption is no longer applied. Warn (not fail)
    # so operators can drop the key from the env file once legacy .enc
    # snapshots have been migrated.
    if env_or("B2_ENCRYPTION_KEY"):
        logger.warning(
            "Using B2 SSE-B2 — client-side encryption key no longer needed. "
            "B2_ENCRYPTION_KEY is deprecated and is ignored by the upload path "
            "(still used only for reading/migrating legacy .enc objects)."
        )

    with_vectors = not args.no_vectors
    payload_only_collections = resolve_payload_only_collections(args.payload_only_collection)
    logger.info(
        "vector_policy requested_vectors=%s payload_only=%s",
        with_vectors,
        ",".join(sorted(payload_only_collections)) or "<none>",
    )

    try:
        collections = list_collections(client)
        # The day's TRUE denominator. Captured from the RAW list and reduced by
        # the canonical (prefer-twin) rule below, so neither a --collection
        # proof run nor a chunk selection can lower it: that is exactly how the
        # 2026-10-23 day advertised expected=4 while 85 collections existed.
        raw_names = list(collections)
        day_universe = len(raw_names)
    except Exception as exc:
        logger.critical("list_collections_failed: %s", exc)
        return 1

    # DAILY SCOPE (2026-10-23). A --collection run is explicit operator intent
    # (the weekly elephant contract uses exactly that), so the daily exclusion
    # applies only to the nightly full-day run. `day_universe` is reduced with
    # it, otherwise the day would be judged against a collection it is
    # contractually not exporting and could never read complete.
    daily_excluded: list[str] = []
    if not args.collection:
        before = len(collections)
        collections, daily_excluded = daily_scope(collections)
        day_universe -= before - len(collections)

    if args.collection:
        requested = set(args.collection)
        missing = requested - set(collections)
        if missing:
            logger.critical("unknown collection(s): %s (have: %s)", sorted(missing), collections)
            return 1
        collections = [c for c in collections if c in requested]
    elif os.getenv("B2_PREFER_TWIN", "1") == "1":
        # 2026-09-28 — operator decision: 8B/4096 is the canonical lane, so the
        # CANONICAL data must be backed up. The old rule excluded `_8b`, which
        # meant the latest snapshot held ZERO _8b objects while the serving
        # store — codebase_reference_v2_8b, 2,126,630 points — had no backup at
        # all. A backup of a lane you are not serving is worthless.
        #
        # But simply including `_8b` would roughly DOUBLE nightly volume, and
        # for nothing: the `_8b` collections are MIRRORS of the base ones (same
        # payload text, different vectors) and vectors are not backed up, so
        # both copies serialise to nearly the same parquet. Backing up both is
        # pure duplication.
        #
        # So the rule is LINEAGE-AWARE rather than a suffix filter: for every
        # base collection, back up its `_8b` twin if one exists, otherwise back
        # up the base. As of this change: 33 of 53 base collections have a twin
        # (base is the retired generation -> backup the twin), and 20 have none
        # (agent_memory, wallet_clusters, scams_v2, youtube_videos, newsletters,
        # token_unlocks, ... -> nothing to substitute, so the base IS the
        # canonical copy and must be backed up).
        #
        # An "exclude all base, keep all _8b" shortcut was rejected: it would
        # silently drop those 20. An "include everything" shortcut was rejected:
        # it doubles volume for byte-identical payloads.
        #
        # Set B2_PREFER_TWIN=0 to restore the legacy suffix-filter behaviour
        # (needed only to reproduce an old snapshot for comparison).
        canonical_suffix = os.getenv("B2_CANONICAL_SUFFIX", "_8b")
        before = len(collections)
        known = set(collections)
        selected: list[str] = []
        twins_used: list[str] = []
        base_kept: list[str] = []
        for name in collections:
            if name.endswith(canonical_suffix):
                # Already a canonical collection — keep it, and suppress its
                # base counterpart if that is also present.
                selected.append(name)
                continue
            twin = name + canonical_suffix
            if twin in known:
                twins_used.append(twin)
            else:
                selected.append(name)
                base_kept.append(name)
        collections = selected
        logger.info(
            "collection_filter prefer_twin suffix=%s in=%d out=%d "
            "twins_selected=%d base_without_twin_kept=%d",
            canonical_suffix,
            before,
            len(collections),
            len(twins_used),
            len(base_kept),
        )
        if base_kept:
            # Log the fallbacks explicitly. A collection silently dropped from
            # the backup is indistinguishable from a collection that was never
            # meant to be backed up, and that is how the 2026-09-26 gap and the
            # zero-_8b snapshots went unnoticed.
            logger.info(
                "no %s twin exists, backing up the base collection directly: %s",
                canonical_suffix,
                ",".join(sorted(base_kept)),
            )
    else:
        configured = args.exclude_suffix
        if configured is None:
            configured = [
                part.strip() for part in os.getenv("B2_EXCLUDE_SUFFIXES", "_8b").split(",")
            ]
        excluded_suffixes = tuple(part for part in configured if part)
        before = len(collections)
        collections = filter_collections(collections, excluded_suffixes)
        logger.info(
            "collection_filter excluded_suffixes=%s excluded=%d remaining=%d",
            ",".join(excluded_suffixes) or "<none>",
            before - len(collections),
            len(collections),
        )

    # The denominator a day is measured against: the canonical set, never the
    # requested subset.
    if os.getenv("B2_PREFER_TWIN", "1") == "1":
        day_universe = canonical_denominator(raw_names)
    logger.info("day_denominator expected_collections=%d", day_universe)

    if not collections:
        logger.warning("no collections to snapshot")
        return 0

    date_str = args.date or datetime.now(UTC).strftime("%Y-%m-%d")

    # ── Chunking (2026-10-23) ─────────────────────────────────────────
    # The whole day does not fit one 6h unit with margin (85 collections,
    # 5.99h measured, SIGTERM'd at 78). Split it into time-bounded chunks so
    # each invocation exits on its own terms and the next one resumes.
    if args.chunk_count:
        # The flag means CHUNK COUNT, so derive the budget from the measured
        # total: N chunks that also respect the wall-clock ceiling. Without the
        # ceiling a chunk could outlive its unit timeout; without the count the
        # day would split into 40 tiny chunks.
        total_est = sum(estimate_minutes(c) for c in collections)
        budget = min(args.max_seconds / 60, max(total_est / args.chunk_count, 1.0))
        plan = plan_chunks(collections, budget_minutes=budget)
        index = args.chunk_index
        if args.auto_chunk or index is None:
            index = 0  # refined after the manifest is read (resume filter below)
        if not 0 <= index < len(plan):
            logger.critical("chunk_index_out_of_range index=%s chunks=%d", index, len(plan))
            return 2
        logger.info(
            "chunk_plan chunks=%d total_est=%.1fmin budget=%.0fmin sizes=%s",
            len(plan),
            sum(c.est_minutes for c in plan),
            args.max_seconds / 60,
            ",".join(f"{c.index}:{len(c.collections)}/{c.est_minutes:.0f}m" for c in plan),
        )
        collections = list(plan[index].collections)
        logger.info(
            "chunk_selected index=%d/%d collections=%d",
            index,
            len(plan) - 1,
            len(collections),
        )
    else:
        plan = []
    # Expected is the whole DAY: not this chunk, not this filter, not this
    # proof run. `len(collections)` was the old answer and it is wrong the
    # moment anything narrows the selection -- a 4-collection proof run at
    # 08:05 stamped expected_collections=4 on a day with 85 live collections,
    # and RugbrainPermanenceDayPartial then compared 4 against 4 and went blind.
    day_total = max(day_universe, sum(len(c.collections) for c in plan))

    logger.info("=== B2 permanence sync started ===")
    logger.info(
        "bucket=%s date=%s collections=%d format=%s vectors=%s",
        bucket_name,
        date_str,
        len(collections),
        fmt,
        with_vectors,
    )

    if args.dry_run:
        for c in collections:
            info = collection_info(client, c)
            logger.info(
                "dry-run would snapshot collection=%s format=%s points=<all> vector_size=%s vectors=%s",
                c,
                fmt,
                info.get("vector_size"),
                collection_has_vectors(c, with_vectors, payload_only_collections),
            )
        # If retention pruning was requested, show what WOULD be pruned.
        # Best-effort: B2 creds may be absent in pure dry-run; never delete.
        if args.prune:
            try:
                b2_api = get_b2_api()
                prune_snapshots(
                    b2_api,
                    bucket_name,
                    dry_run=True,
                    keep_daily=args.keep_daily,
                    keep_weekly=args.keep_weekly,
                    keep_monthly=args.keep_monthly,
                )
            except Exception as exc:
                logger.warning("prune_dry_run_skipped: %s", exc)
        logger.info("=== dry-run complete (no uploads, no deletes) ===")
        return 0

    # Authorize B2 + ensure bucket (after dry-run so no creds needed to list)
    try:
        b2_api = get_b2_api()
        ensure_bucket(b2_api, bucket_name)
    except Exception as exc:
        logger.critical("b2_auth_failed: %s", exc)
        return 2

    # Resume + auto-chunk: today's manifest is the source of truth for what is
    # already landed, so a rerun -- from any chunk, after any kill -- does not
    # re-export or duplicate. Idempotent by construction: the same object key is
    # overwritten and the manifest merge replaces that collection's entry.
    # One manifest read feeds both the done-set and the per-shard resume state.
    prior_entries = (
        {} if args.no_resume
        else todays_manifest_entries(b2_api, bucket_name, date_str)
    )
    done_today = {n for n, e in prior_entries.items() if entry_is_done(e)}
    partial_today = {n: e for n, e in prior_entries.items() if not entry_is_done(e)}
    if partial_today:
        logger.info(
            "shard_resume available collections=%s",
            ",".join(
                f"{n}({len(e.get('shards') or [])}shards/" f"{sum(int(x.get('point_count', 0) or 0) for x in e.get('shards') or [])}pts)"
                for n, e in sorted(partial_today.items())
            ),
        )
    # ── Date rollover: CARRY FORWARD (2026-10-23) ────────────────────
    # The manifest is keyed by UTC date, so a day that does not finish used to
    # strand its work: 2026-10-23 reached 79/85 and 2026-10-24 would have begun
    # at 0/85 and re-exported all 79. A collection whose object is already in
    # B2 does not stop being backed up at midnight. Today's done-set is
    # therefore the UNION of today and the previous `--carry-days` manifests;
    # each inherited entry records `carried_from` = the date it really landed.
    # The older manifest stays immutable (it is the record of that day);
    # today's manifest is the day that closes the gap, and `complete` is
    # computed over the union. Without this the date key turns every partial
    # day into permanent lost work.
    carried: dict[str, dict[str, Any]] = {}
    carry_from: list[str] = []
    if not args.no_resume and not args.no_carry:
        recent = [d for d in snapshot_dates_on_remote(b2_api, bucket_name) if d < date_str]
        live_universe = set(raw_names)
        # `recent[-0:]` is `recent[0:]`: --carry-days 0 (the nightly default)
        # inherited EVERY dated manifest back to 2026-09-*, then the
        # self-contained-day rule immediately dropped those entries as stale.
        # Measured 2026-10-24 03:00/03:40/04:20: 29 manifests downloaded per
        # firing, ~50 entries carried, ~50 dropped, zero durable progress.
        # No carry means no carry: slice only when the count is positive.
        carry_window = [d for d in recent[-int(args.carry_days) :]] if int(args.carry_days) > 0 else []
        for prior_date in reversed(carry_window):
            for name, entry in todays_manifest_entries(b2_api, bucket_name, prior_date).items():
                if name in prior_entries or name in carried or not entry_is_done(entry):
                    continue
                if name not in live_universe:
                    # A collection deleted from Qdrant must not be inherited:
                    # it would inflate `landed` toward a complete that is false.
                    continue
                inherited = dict(entry)
                inherited["carried_from"] = prior_date
                carried[name] = inherited
            if carried:
                carry_from.append(prior_date)
        if carried:
            prior_entries.update(carried)
            logger.info(
                "day_carry_forward date=%s from=%s carried=%d (a partial day does "
                "not restart at 0)",
                date_str,
                ",".join(carry_from),
                len(carried),
            )

    # A carried entry naming a collection that no longer exists in Qdrant must
    # be DROPPED, not merged: it raises `expected` (85 -> 86 on 2026-10-23 via
    # cbrv2_8b_hnswdisk_pilot) and a day can then never report complete.
    # An out-of-scope entry is NOT stale. rag_documents_8b is filtered out of
    # raw_names by the _8b twin rule, so the nightly classified the weekly
    # elephant as a deleted collection and DROPPED its shard cursor. The
    # elephant fires Sunday 06/07/08/09 while the daily timer fires 06:20,
    # 07:00, 07:40, 08:20 -- every one of those erasures reset the weekly
    # contract to part-0, so its 23 shards could never accumulate. Measured
    # 2026-10-24: one real elephant shard landed (1,243.7 MB) and the next
    # daily run removed the entry that named it.
    weekly_scope = set(daily_scope([])[1])
    stale = {
        n for n in prior_entries if n not in set(raw_names) and n not in weekly_scope
    }
    if not args.carry_days:
        # SELF-CONTAINED DAY. An entry inherited from another date is not this
        # day's backup, even though it is non-partial: 2026-10-23's manifest was
        # caught holding three 2026-10-20 entries (codebase_reference_v2_8b,
        # threat_intel_v2_8b_hybrid, wallet_profiles_8b) with NO object under
        # snapshots/2026-10-23/ at all, i.e. `complete` would have been a lie
        # and a restore would have silently served 3-day-old data. Drop them so
        # they are re-exported today.
        inherited = {n for n, e in prior_entries.items() if e.get("carried_from")}
        if inherited:
            logger.warning(
                "manifest_not_self_contained reexporting=%s", ",".join(sorted(inherited))
            )
        stale |= inherited
        for n in inherited:
            prior_entries.pop(n, None)
        if inherited:
            # Recompute: done_today was built from the pre-drop map, and an
            # inherited entry must NOT excuse today's export.
            done_today = {n for n, e in prior_entries.items() if entry_is_done(e)}
            partial_today = {n: e for n, e in prior_entries.items() if not entry_is_done(e)}
    if stale:
        logger.warning("manifest_stale_entries dropped=%s", ",".join(sorted(stale)))

    if plan and args.auto_chunk:
        # CHEAPEST CHUNK FIRST (2026-10-23). This used to be `next(...)`: the
        # plan is first-fit-DECREASING, so chunk 0 is always the elephant
        # (rag_documents_8b, ~60 min est / 192 min measured). Picking it first
        # meant every firing spent its whole budget on the one collection that
        # cannot fit, and the day sat at 4/85 for hours while 84 small
        # collections waited behind it. Selecting the least expensive
        # incomplete chunk makes progress monotonic and gates the day on the
        # cheap work, leaving the elephant for last (and for its own slots).
        # Rank by what the chunk still OWES, not by its whole planned cost:
        # a 30-min chunk holding one 1-min collection left is 1 min of work,
        # and charging it 30 min made the runner prefer a 16-min chunk over a
        # 1-min one. Cheapest-by-remaining is what actually keeps momentum.
        def owed(chunk: Any) -> float:
            return math.fsum(
                estimate_minutes(n) for n in chunk.collections if n not in done_today
            )

        first_open = min(
            (c for c in plan if owed(c) > 0),
            key=lambda c: (owed(c), c.index),
            default=None,
        )
        if first_open is None:
            logger.info(
                "day_already_complete date=%s collections=%d — nothing to do",
                date_str,
                len(done_today),
            )
            if stale:
                # Repair the day in place: without this the stale name keeps
                # `expected` at 86 and a complete day can never say so.
                upload_manifest(
                    b2_api,
                    bucket_name,
                    date_str,
                    [],
                    expected_count=day_total,
                    missing={},
                    drop=stale,
                )
            return 0
        collections = list(first_open.collections)
        logger.info(
            "chunk_auto_selected index=%d/%d owed=%.1fmin planned=%.1fmin "
            "collections=%d open=%d",
            first_open.index,
            len(plan) - 1,
            owed(first_open),
            first_open.est_minutes,
            len(first_open.collections),
            sum(1 for n in first_open.collections if n not in done_today),
        )
        if owed(first_open) > args.max_seconds / 60:
            # DEDICATED ELEPHANT SLOT. This chunk cannot finish in one firing;
            # it is chosen only because nothing cheaper is left, and it will
            # land whole shards and resume. It is never mixed into a cheap
            # chunk, and it is always LAST (fewest cheap collections first).
            logger.warning(
                "chunk_elephant_slot index=%d owed=%.1fmin budget=%.1fmin "
                "collections=%s (shard-bounded, resumes at cursor)",
                first_open.index,
                owed(first_open),
                args.max_seconds / 60,
                ",".join(first_open.collections),
            )
    if done_today:
        skipped = [c for c in collections if c in done_today]
        collections = [c for c in collections if c not in done_today]
        logger.info(
            "resume date=%s already_done=%d skipped_in_this_chunk=%d remaining=%d",
            date_str,
            len(done_today),
            len(skipped),
            len(collections),
        )
    if not collections:
        logger.info("=== nothing left to snapshot for %s (resume) ===", date_str)
        return 0

    expected_count = day_total

    live_points: dict[str, int] = {}

    def points_of(name: str) -> int:
        if name not in live_points:
            live_points[name] = collection_points(client, name)
        return live_points[name]

    def next_unit_seconds(name: str) -> float:
        """Seconds the next RESUMABLE unit of *name* needs.

        One shard when the collection cannot finish (or is already partial),
        the whole remaining collection otherwise. The elephant is judged by
        the shard it would write, never by its 115-minute total -- that
        distinction is what lets an elephant slot make durable progress
        instead of being skipped as unaffordable.
        """
        prior = max(estimate_minutes(name) * 60.0, MIN_ESTIMATE_S)
        live = points_of(name)
        state = resume_state(prior_entries.get(name))
        done = int(state.get("points_done") or 0)
        if live > 0 and done > 0:
            prior = max(prior * (1.0 - min(done / live, 1.0)), MIN_ESTIMATE_S)
        if args.shard_points and live > 0:
            rate = max(estimate_minutes(name) * 60.0, MIN_ESTIMATE_S) / live
            return min(prior, max(rate * args.shard_points, MIN_ESTIMATE_S))
        return prior

    def flush(chunk_entries: list[dict[str, Any]], chunk_failures: dict[str, str]) -> None:
        # REPAIR ON RESUME: an entry inherited from an earlier day (or written by
        # a run whose point count failed) gets its denominator pinned here, so a
        # day that was already partially landed becomes completable without
        # re-exporting a single shard.
        for entry in chunk_entries:
            if entry.get("shards") and not entry.get("expected_shards"):
                pin_expected_shards(
                    entry,
                    points_of(str(entry.get("collection") or "")),
                    int(args.shard_points or 0),
                )
        upload_manifest(
            b2_api,
            bucket_name,
            date_str,
            chunk_entries,
            expected_count=expected_count,
            missing=chunk_failures,
            drop=stale,
            exclude_from_daily=not bool(args.collection),
        )

    def snap(name: str) -> dict[str, Any]:
        state = resume_state(prior_entries.get(name))

        def partial_shards(shards_now: list[dict[str, Any]], points_now: int) -> None:
            """Persist shard progress to the manifest as each shard lands."""
            entry: dict[str, Any] = {
                "collection": name,
                "format": fmt,
                "partial": True,
                "point_count": points_now,
                "bytes_stored": sum(
                    int(x.get("bytes_stored", 0) or 0) for x in shards_now
                ),
                "shards": list(shards_now),
                "started_at": datetime.now(UTC).isoformat(),
            }
            # The denominator belongs on EVERY entry that names shards, not only
            # on the one that reaches the end of the export. The chunk budget
            # stops the elephant mid-collection every night, so this flush is
            # the only writer that ever runs for it -- measured 2026-10-23: 0 of
            # 5 sharded entries carried expected_shards, so none could ever be
            # judged finished.
            pin_expected_shards(entry, points_of(name), int(args.shard_points or 0))
            # FEED THE CURSOR BACK INTO THIS PROCESS (2026-10-23). `state` is
            # read from prior_entries once, at the top of snap(); a retry of
            # the same collection therefore restarted at part 0 and re-uploaded
            # 5 GB (observed twice today: part-0 at 11:05 and again at 13:23).
            # Writing the entry back makes a retry resume at the next shard.
            prior_entries[name] = entry
            try:
                upload_manifest(
                    b2_api,
                    bucket_name,
                    date_str,
                    [entry],
                    expected_count=expected_count,
                    missing={},
                )
            except Exception as exc:
                logger.warning("shard_flush_failed collection=%s err=%s", name, exc)

        return snapshot_collection(
            client,
            b2_api,
            bucket_name,
            date_str,
            name,
            workdir,
            args.limit,
            collection_has_vectors(name, with_vectors, payload_only_collections),
            fmt,
            shard_points=args.shard_points,
            resume=state,
            on_shard=partial_shards,
            deadline=deadline_monotonic,
        )

    if carried:
        # Persist the inherited entries NOW, so today's manifest already covers
        # them even if this firing lands nothing else.
        try:
            flush(list(carried.values()), {})
        except Exception as exc:
            logger.warning("carry_flush_failed err=%s", exc)

    deadline_monotonic = time.monotonic() + float(args.max_seconds)
    with tempfile.TemporaryDirectory(prefix="b2-permanence-") as workdir_str:
        workdir = Path(workdir_str)
        result = run_chunk(
            collections,
            snapshot_fn=snap,
            flush_fn=flush,
            budget_seconds=float(args.max_seconds),
            estimate_fn=next_unit_seconds,
            hard_seconds=float(args.slot_seconds),
        )
    if result.skipped:
        # The slot was refused, not spent: nothing affordable fitted. Exiting 0
        # keeps the timer healthy; the log line is the operator's signal that
        # the per-shard size or the slot length is wrong.
        logger.error(
            "chunk_no_progress_slot_skipped remaining=%d hard=%.1fmin budget=%.1fmin",
            len(collections),
            args.slot_seconds / 60,
            args.max_seconds / 60,
        )

    # Final flush records deferred work as owed, so a short run is visible in
    # the manifest rather than passing as a complete day.
    deferred_missing = {n: "deferred: chunk budget exhausted (next chunk)" for n in result.deferred}
    try:
        upload_manifest(
            b2_api,
            bucket_name,
            date_str,
            [],
            expected_count=expected_count,
            missing={**result.failed, **deferred_missing},
        )
    except Exception as exc:
        logger.error("manifest_failed: %s", exc)

    # Retention pruning — gated: only runs with --prune; dry-run unless
    # --prune-live AND NOT --dry-run (--dry-run already returned above).
    # Never prune from a partial day: retention counts dated dirs, and a
    # half-written day would be counted as a retained day.
    if args.prune and not result.deferred and not result.failed:
        try:
            prune_snapshots(
                b2_api,
                bucket_name,
                dry_run=not args.prune_live,
                keep_daily=args.keep_daily,
                keep_weekly=args.keep_weekly,
                keep_monthly=args.keep_monthly,
            )
        except Exception as exc:
            logger.error("prune_failed: %s", exc)

    logger.info(
        "=== B2 permanence sync complete: %d ok, %d failed, %d deferred, %d points, %.1fmin ===",
        len(result.done),
        len(result.failed),
        len(result.deferred),
        result.points,
        result.elapsed_s / 60,
    )
    # Deferred work is the DESIGNED outcome of a chunked run: the day owes
    # collections, the manifest says so, and the next firing resumes at the
    # exact shard. Exiting non-zero here marked every honest partial chunk a
    # FAILED unit (8 of them in 26h on 2026-10-23), which is monitoring noise
    # pointing at the wrong thing. Only a real failure is exit 1.
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
