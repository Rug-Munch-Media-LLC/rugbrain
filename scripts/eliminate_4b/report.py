"""Reporting for the 2560 -> 4096 twin fill: the per-collection table and ETA.

Prints exactly what WOULD be embedded per collection, and after a real run
what WAS, with before/after counts. A collection that does not reach parity
is reported FAILED, never quietly passed over.
"""

from __future__ import annotations

from .plan import Report

HDR = (
    f"{'collection':24s} {'base':>9} {'twin_bef':>9} {'to_embed':>9} "
    f"{'embedded':>8} {'twin_aft':>9} {'secs':>7} status"
)


def print_table(rows: list[Report], executed: bool) -> None:
    print(HDR)
    print("-" * len(HDR))
    tot_missing = tot_embedded = 0
    for r in rows:
        embedded = r.embedded if executed else 0
        after = r.twin_after if executed else r.twin_before
        tot_missing += r.to_embed
        tot_embedded += embedded
        print(
            f"{r.base:24s} {r.base_count:>9} {r.twin_before:>9} {r.to_embed:>9} "
            f"{embedded:>8} {after:>9} {r.seconds:>7.1f} {r.status}"
        )
    print("-" * len(HDR))
    print(f"{'TOTAL':24s} {'':>9} {'':>9} {tot_missing:>9} {tot_embedded:>8}")
    for r in rows:
        for f in r.failed:
            print(f"  !! {r.base}: {f}")


def print_eta(rows: list[Report], rate: float) -> None:
    missing = sum(r.to_embed for r in rows)
    if not missing or rate <= 0:
        return
    hours = missing / rate / 3600
    h, m = divmod(int(hours * 60), 60)
    print(f"\nTHROUGHPUT (measured this run): {rate:.1f} points/s")
    print(f"REMAINING TO EMBED: {missing}")
    print(f"FULL-RUN ETA: ~{h}h{m:02d}m at that rate")
    print(
        "(DeepInfra is the bottleneck. A re-run after a crash re-embeds only"
        " the still-missing tail, because the diff is by point id.)"
    )
