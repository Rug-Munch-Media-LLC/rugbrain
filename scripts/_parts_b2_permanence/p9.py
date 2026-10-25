"""Part 9/9 of scripts/b2_permanence.py."""

from __future__ import annotations

from _parts_b2_permanence._h1 import *  # noqa: F401,F403
from _parts_b2_permanence.p1 import collection_has_vectors, collection_info, filter_collections, list_collections, resolve_payload_only_collections, resolve_qdrant_client
from _parts_b2_permanence.p2 import collection_points
from _parts_b2_permanence.p3 import snapshot_collection
from _parts_b2_permanence.p5 import upload_manifest
from _parts_b2_permanence.p6 import snapshot_dates_on_remote, todays_manifest_entries
from _parts_b2_permanence.p7 import prune_snapshots
from _parts_b2_permanence.p8 import build_parser, canonical_denominator

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


__all__ = [
    "main",
]
