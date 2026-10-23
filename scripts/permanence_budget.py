#!/usr/bin/env python3
"""Preemptive wall-clock budget for ONE permanence chunk.

Why this module exists (MEASURED 2026-10-23 15:00Z). The chunk budget was
tested only BETWEEN collections. One collection (news_v2_8b, 3 shards) spent
43.3 min against a 30-min budget, so the 15:40 timer slot was LOST -- systemd
never starts a oneshot that is still active -- and wallet_profiles_8b was
deferred having landed zero shards. Nineteen slots at 40 min cannot deliver
nineteen chunks while a chunk may overrun its slot: every overrun burns the
next slot outright.

The rule here is AFFORDABILITY, not elapsed time:

    start a unit only if  elapsed + estimate*SAFETY + tail <= hard_limit

``tail`` is the unbounded end of any unit already in flight: close the parquet
writer, sha256 it, upload it. A yield is only honest when the yield fits.

Two limits, because they answer different questions:

  soft  (``B2_CHUNK_SECONDS``, 1800 s) — when an in-flight collection should
        stop scrolling and land what it has (checked per scroll batch by the
        writer's ``should_stop``).
  hard  (``B2_SLOT_SECONDS``, 2100 s) — when a NEW unit may not be started.
        It sits 5 min inside the 40-min timer slot, so a slot never overruns
        and never takes the next slot down with it.

A retry is a unit too: its backoff sleep AND the re-export it starts.

One primary type: ``Slice``.
"""

from __future__ import annotations

import time

# Estimate inflation. The per-collection priors are measured, but a B2 upload
# slows down under contention; 15% covers a normal bad night.
SAFETY = 1.15
# End-of-unit work that no estimate accounts for: close + sha256 + upload of
# ONE shard. At B2_SHARD_POINTS=50000 of 4096-dim vectors that is ~1.3 GB and
# ~90 s measured; 150 s leaves room for a slow upload.
TAIL_S = 150.0


class Slice:
    """One chunk's wall clock: a soft stop and a hard start limit."""

    def __init__(self, soft_seconds: float, hard_seconds: float, started: float | None = None):
        self.soft = float(soft_seconds)
        self.hard = max(float(hard_seconds), float(soft_seconds))
        self.started = time.monotonic() if started is None else started

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining(self) -> float:
        """Seconds left before the soft stop (what the writer yields against)."""
        return self.soft - self.elapsed()

    def can_start(self, estimate_s: float) -> bool:
        """True when a NEW unit of ~estimate_s finishes inside the hard limit.

        ``estimate_s`` must describe the next RESUMABLE unit -- one shard of a
        collection that cannot finish, the whole collection otherwise -- so the
        elephant is judged by the shard it would write, not by its 80-min
        total. That is what lets an elephant slot make progress.
        """
        return self.elapsed() + max(float(estimate_s), 0.0) * SAFETY + TAIL_S <= self.hard

    def can_retry(self, estimate_s: float, backoff_s: float) -> bool:
        """A retry is affordable only if its sleep AND its work both fit."""
        return (
            self.elapsed() + float(backoff_s) + max(float(estimate_s), 0.0) * SAFETY + TAIL_S
            <= self.hard
        )
