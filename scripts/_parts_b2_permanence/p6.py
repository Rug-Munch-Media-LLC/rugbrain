"""Part 6/9 of scripts/b2_permanence.py."""

from __future__ import annotations

from _parts_b2_permanence._h1 import *  # noqa: F401,F403

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


__all__ = [
    "todays_done_collections",
    "todays_manifest_entries",
    "parse_snapshot_date",
    "snapshot_dates_on_remote",
    "retention_keep_set",
]
