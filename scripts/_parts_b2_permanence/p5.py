"""Part 5/9 of scripts/b2_permanence.py."""

from __future__ import annotations

from _parts_b2_permanence._h1 import *  # noqa: F403
from _parts_b2_permanence.p4 import _merge_shard_progress


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


__all__ = [
    "upload_manifest",
]
