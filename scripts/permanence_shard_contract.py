#!/usr/bin/env python3
"""Pin a sharded collection's shard denominator onto its manifest entry.

THE DEFECT (2026-10-23): `expected_shards` was computed only on the path that
reaches the end of ``snapshot_collection``. The elephant is never allowed to
reach the end — the chunk budget stops it mid-collection every night — so the
only entry that ever reached the manifest came from the incremental
``partial_shards`` flush, which carried no denominator. Measured on
2026-10-23: 0 of 5 sharded entries had ``expected_shards``, including
``rag_documents_8b`` (650,000 of 1,136,363 points, 10 shards landed, 23 owed).

Without a numerator/denominator pair the day can never be judged complete: the
entry stays ``partial: true`` forever and ``entry_is_done()`` stays false, so
the same collection is re-entered every firing and no amount of progress ever
finishes it. ``expected_shards`` is what turns "partial" into a countable
remainder — which is the difference between a day that completes after N
firings and one that never completes at all.

One primary function: `pin_expected_shards`.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("permanence_shard_contract")


def pin_expected_shards(
    entry: dict[str, Any], total_points: int, shard_points: int
) -> dict[str, Any]:
    """Record ceil(total/shard_points) on `entry`; never lower an existing value.

    Idempotent and monotonic on purpose:

    * a resumed firing recomputes the same number, so re-pinning is a no-op;
    * if the live collection GREW since the first pin, the larger denominator
      wins — the day then owes more shards instead of silently declaring
      itself complete;
    * if the count is unavailable (0), the value already on the entry is kept,
      because a failed measurement is not evidence that the denominator shrank.
    """
    if not entry or not shard_points or shard_points <= 0:
        return entry
    prior = int(entry.get("expected_shards") or 0)
    if total_points and total_points > 0:
        computed = max(1, -(-int(total_points) // int(shard_points)))
    else:
        computed = prior
    chosen = max(prior, computed)
    if not chosen:
        logger.warning(
            "expected_shards_unknown collection=%s total=%s shard_points=%s",
            entry.get("collection"),
            total_points,
            shard_points,
        )
        return entry
    if chosen != prior:
        entry["expected_shards"] = chosen
        logger.info(
            "expected_shards_pinned collection=%s expected=%d total=%s shard_points=%s",
            entry.get("collection"),
            chosen,
            total_points,
            shard_points,
        )
    return entry
