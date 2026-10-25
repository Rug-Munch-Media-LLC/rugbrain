"""Part 7/9 of scripts/b2_permanence.py."""

from __future__ import annotations

from _parts_b2_permanence._h1 import *  # noqa: F401,F403
from _parts_b2_permanence.p6 import parse_snapshot_date, retention_keep_set, snapshot_dates_on_remote

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


__all__ = [
    "prune_snapshots",
]
