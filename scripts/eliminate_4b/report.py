"""Reporting for the 2560 -> 4096 twin fill: the per-collection table and ETA.

Prints exactly what WOULD be embedded per collection — base count, twin count,
to-embed, and the count the twin must reach — and after a real run what WAS,
with before/after counts. A collection that does not reach parity is reported
FAILED, never quietly passed over.
"""

from __future__ import annotations

from .plan import Report
from .targets import EXCLUDED

HDR = (
    f"{'collection':22s} {'base':>9} {'twin_bef':>9} {'to_embed':>9} "
    f"{'expect':>9} {'embedded':>8} {'twin_aft':>9} {'secs':>6} status"
)


def print_table(rows: list[Report], executed: bool) -> None:
    """``expect`` is the post-run count a complete run must reach.

    In a dry run it is the number to check the real run against, so the
    operator can tell afterwards whether anything was silently dropped.
    """
    print(HDR)
    print("-" * len(HDR))
    tot_missing = tot_embedded = tot_expect = 0
    for r in rows:
        embedded = r.embedded if executed else 0
        after = r.twin_after if executed else r.twin_before
        tot_missing += r.to_embed
        tot_embedded += embedded
        tot_expect += r.expected_after
        print(
            f"{r.base:22s} {r.base_count:>9} {r.twin_before:>9} {r.to_embed:>9} "
            f"{r.expected_after:>9} {embedded:>8} {after:>9} {r.seconds:>6.1f} {r.status}"
        )
    print("-" * len(HDR))
    print(f"{'TOTAL':22s} {'':>9} {'':>9} {tot_missing:>9} {tot_expect:>9} {tot_embedded:>8}")
    for r in rows:
        if r.unrecoverable:
            print(
                f"  -- {r.base}: {r.unrecoverable} point(s) skipped, no recoverable "
                "embed input (never embedded)"
            )
        for f in r.failed:
            print(f"  !! {r.base}: {f}")


def print_excluded(counts: dict[str, int] | None = None) -> None:
    """Name what is deliberately NOT being embedded, and why.

    Silence here is what let the earlier 60,255-point plan look reasonable:
    52k of those points were never embeddable at all.
    """
    print("\nEXCLUDED — no text field in the payload, no recoverable embed input:")
    for name, why in EXCLUDED:
        n = f" ({counts[name]:,} points)" if counts and name in counts else ""
        print(f"  {name:22s}{n}  {why}")
    print("  -> going to ClickHouse as address-keyed lookup data, not re-embedded.")


def print_verdict(rows: list[Report], executed: bool) -> bool:
    """State, plainly, what may be cut — and refuse to state it otherwise."""
    if not executed:
        print("\nDRY RUN. Nothing was written. Re-run with --execute to fill.")
        return True
    ok = [r for r in rows if r.status == "OK" and r.parity]
    bad = [r for r in rows if r.status not in ("OK", "SKIP")]
    print(
        f"\nVERIFIED {len(ok)}/{len(rows)} collections at or above base parity."
        " This tool DELETED NOTHING."
    )
    if bad:
        print("The following are NOT droppable — parity not reached:")
        for r in bad:
            print(f"  {r.base}: {r.status}")
    print("Deletion of any 2560 base is a human decision, made after reading this.")
    return not bad
