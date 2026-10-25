"""Part 8/9 of scripts/b2_permanence.py."""

from __future__ import annotations

from _parts_b2_permanence._h1 import *  # noqa: F401,F403

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


__all__ = [
    "build_parser",
    "_fmt_size",
    "canonical_denominator",
]
