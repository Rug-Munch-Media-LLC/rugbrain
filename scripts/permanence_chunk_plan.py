#!/usr/bin/env python3
"""Plan a whole-day permanence run as time-bounded chunks.

Why this module exists: the 2026-10-23 run spent 5.99 h across 85 collections,
hit `TimeoutStartSec=21600`, and was SIGTERM'd at [78/85] -- for the fourth
night running. A single invocation covering the whole day has no margin on ANY
night, because the cost is not stable: `rag_documents_8b` measured 114.6 min on
2026-10-20 and was still running at ~170 min when it 503'd on 2026-10-23.

So the day is split into chunks that each fit a fraction of the budget. The
split is FIRST-FIT-DECREASING over MEASURED minutes, not a round-robin over
names: the cost is extremely skewed (5 collections = 94% of the run), so an
even split by count would hand one chunk all the elephants.

The weights below come from the per-collection `started_at`/`finished_at` pairs
in `snapshots/2026-10-20/manifest.json` (85 entries, 263.5 min total), with
`rag_documents_8b` set to its post-shard cost. They are a PRIOR, not a promise:
`run_chunk` enforces a monotonic deadline, so a wrong prior costs an early stop,
never a SIGTERM.

One primary function: `plan_chunks`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

# Measured minutes per collection (2026-10-20 manifest). Collections absent
# from this table cost <= 0.1 min in every observed run; DEFAULT_MIN is
# deliberately above that so unknowns can never be silently free.
MEASURED_MIN: dict[str, float] = {
    # rag_documents_8b is the 114.6 min / 29.8 GB WHOLE-collection measurement
    # (2026-10-20 manifest). It is deliberately NOT the post-shard 60 min that
    # used to sit here: the estimate must be a RATE (114.6 min / 1,136,447 pts
    # = 6.0 ms/pt), because the shard budget check multiplies that rate by
    # B2_SHARD_POINTS to decide whether one shard fits the slot. The old 60 min
    # under-estimated a 100k shard by half (predicted 5.3 min, measured 10.9).
    "rag_documents_8b": 114.6,
    # 2026-10-23 manifest, measured start->finish with sharding on:
    "threat_intel_v2_8b__sparsemig": 23.7,
    "threat_intel_v2_8b_hybrid": 16.5,
    "codebase_reference_v2_8b": 13.6,  # payload-only: 3.7M pts, 1.6 GB
    "news_v2_8b": 13.4,
    "scam_intel_v2_8b": 12.8,
    "scan_intel_8b_hybrid": 6.7,
    "scan_intel_8b": 5.2,
    "known_scams_8b_hybrid": 5.2,
    "wallet_profiles_8b_hybrid": 3.5,
    "defi_exploits_8b_hybrid": 3.5,
    "known_scams_8b": 3.2,
    "live_threats_8b_hybrid": 2.4,
    "wallet_profiles_8b": 2.2,
    "defi_exploits_8b": 2.2,
    "social_intel_8b_hybrid": 1.5,
    "social_intel_8b": 1.0,
}
DEFAULT_MIN = 0.3
# A chunk may not exceed the budget by more than this factor. One oversized
# collection is allowed to run into the next chunk's allowance (it cannot be
# split further), but two are not.
OVERSIZE_FACTOR = 1.25


@dataclass
class Chunk:
    """One systemd invocation's worth of work."""

    index: int
    collections: list[str] = field(default_factory=list)
    est_minutes: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "collections": list(self.collections),
            "est_minutes": round(self.est_minutes, 1),
        }


def estimate_minutes(name: str) -> float:
    """Prior cost for one collection, in minutes."""
    return MEASURED_MIN.get(name, DEFAULT_MIN)


def plan_chunks(collections: Iterable[str], budget_minutes: float = 80.0) -> list[Chunk]:
    """Split *collections* into first-fit-decreasing chunks of ~*budget_minutes*.

    Deterministic: the same collection list and budget always produce the same
    plan, so a planner run on the writer and a planner run on an operator's
    shell agree. Ordering within a chunk keeps the original (name-sorted) order
    for reproducible logs.
    """
    names = list(collections)
    order = {name: i for i, name in enumerate(names)}
    sized = sorted(names, key=lambda n: (-estimate_minutes(n), n))

    chunks: list[Chunk] = []
    for name in sized:
        cost = estimate_minutes(name)
        # An item costing more than the whole budget still gets a chunk of its
        # own (it cannot be split by the planner — sharding is the writer's
        # job), it is just never packed next to anything else.
        target = next((c for c in chunks if c.est_minutes + cost <= budget_minutes), None)
        if target is None:
            target = Chunk(index=len(chunks))
            chunks.append(target)
        target.collections.append(name)
        target.est_minutes += cost

    for chunk in chunks:
        chunk.collections.sort(key=lambda n: order[n])
    return chunks


def total_minutes(chunks: Iterable[Chunk]) -> float:
    """Sum of the per-chunk estimates — the expected whole-day wall clock."""
    return math.fsum(c.est_minutes for c in chunks)
