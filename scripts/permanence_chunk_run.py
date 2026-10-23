#!/usr/bin/env python3
"""Execute ONE time-bounded chunk of the permanence run, resumably.

Four invariants:

1. **The budget is PREEMPTIVE, not a between-collection check.** Before every
   collection AND every retry the runner asks ``Slice.can_start``/
   ``can_retry``: "does this unit, its estimate +15% and the tail that lands
   it, finish inside the slot?" A unit that cannot finish is never started.
   MEASURED 2026-10-23 15:00Z: a 43.3-min chunk against a 30-min budget lost
   the 15:40 slot outright (systemd will not start an active oneshot) and
   deferred wallet_profiles_8b with zero shards landed.
2. **A slot that can add nothing must not start** — ``skipped`` instead of
   burning a slot to defer 85 names. That is what starved the cheap work.
3. **Progress survives a kill** — flush after EVERY collection, not at the end.
   Root cause of 2026-10-21/22/23: 86 objects in B2 and no manifest.
4. **A failure is recorded, not swallowed** — ``missing[]`` with the error.

One primary function: `run_chunk`.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from permanence_budget import Slice

logger = logging.getLogger("permanence_chunk")

# Retries are for TRANSIENT faults only (503 no-tomes, reset, read timeout).
# b2sdk retries uploads 5x internally; this is the layer above it, and each
# attempt is affordable or not attempted at all.
RETRY_ATTEMPTS = 3
RETRY_BASE_SECONDS = 5.0
RETRY_MAX_SECONDS = 60.0


@dataclass
class ChunkResult:
    """What one chunk actually did — evidence for the manifest and the logs."""

    done: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    deferred: list[str] = field(default_factory=list)
    points: int = 0
    shards: int = 0
    elapsed_s: float = 0.0
    skipped: bool = False

    @property
    def progress(self) -> int:
        """Durable units landed: completed collections + shards already in B2."""
        return len(self.done) + self.shards

    @property
    def ok(self) -> bool:
        return not self.failed and not self.deferred

    def summary(self) -> str:
        return (
            f"done={len(self.done)} failed={len(self.failed)} "
            f"deferred={len(self.deferred)} shards={self.shards} "
            f"points={self.points} skipped={self.skipped} "
            f"elapsed={self.elapsed_s / 60:.1f}min"
        )


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter — never a thundering herd on recovery."""
    delay = RETRY_BASE_SECONDS * (2**attempt)
    return min(delay + random.uniform(0, RETRY_BASE_SECONDS), RETRY_MAX_SECONDS)


def run_chunk(
    collections: list[str],
    *,
    snapshot_fn: Callable[[str], dict],
    flush_fn: Callable[[list[dict], dict[str, str]], None],
    budget_seconds: float,
    estimate_fn: Callable[[str], float] | None = None,
    hard_seconds: float | None = None,
) -> ChunkResult:
    """Snapshot *collections* until the slot is spent, flushing as it goes.

    ``snapshot_fn(name)`` must return one manifest entry (and must be safe to
    call again for the same collection: it overwrites the same object key).
    ``flush_fn(entries, failures)`` must merge-publish the day's manifest; it is
    called after every collection so a SIGTERM costs at most one collection.
    ``estimate_fn(name)`` returns the seconds the next RESUMABLE unit needs.
    """
    result = ChunkResult()
    entries: list[dict] = []
    budget = Slice(budget_seconds, hard_seconds if hard_seconds else budget_seconds)

    def cost(name: str) -> float:
        try:
            return float(estimate_fn(name)) if estimate_fn else 0.0
        except Exception as exc:
            logger.warning("chunk_estimate_failed collection=%s err=%s", name, exc)
            return 0.0

    for position, name in enumerate(collections):
        estimate = cost(name)
        if not budget.can_start(estimate):
            result.deferred = collections[position:]
            if result.progress == 0:
                # Nothing durable landed and nothing affordable: a slot spent
                # here buys nothing. Refuse it instead of deferring 85 names.
                result.skipped = True
                result.deferred = list(collections)
                logger.error(
                    "chunk_slot_skipped elapsed=%.1fmin hard=%.1fmin next=%s est=%.1fmin"
                    " (slot not started)", budget.elapsed() / 60, budget.hard / 60,
                    name, estimate / 60,
                )
                break
            logger.warning(
                "chunk_budget_gate elapsed=%.1fmin soft=%.1fmin hard=%.1fmin next=%s"
                " est=%.1fmin deferred=%d %s", budget.elapsed() / 60, budget.soft / 60,
                budget.hard / 60, name, estimate / 60, len(result.deferred),
                ",".join(result.deferred[:10]),
            )
            break

        logger.info(
            "chunk_item %d/%d collection=%s elapsed=%.1fmin est=%.1fmin",
            position + 1, len(collections), name, budget.elapsed() / 60, estimate / 60,
        )
        error = ""
        for attempt in range(RETRY_ATTEMPTS + 1):
            try:
                entry = snapshot_fn(name)
                if entry.get("deferred"):
                    # Shard budget yield: shards are in B2, the collection is
                    # partial. Owed work — never counted as done.
                    result.deferred.append(name)
                    result.points += int(entry.get("point_count", 0) or 0)
                    result.shards += len(entry.get("shards") or [])
                    logger.warning(
                        "chunk_yielded collection=%s shards_in_b2=%d points=%d", name,
                        len(entry.get("shards") or []), int(entry.get("point_count", 0) or 0),
                    )
                    error = ""
                    break
                entries.append(entry)
                result.done.append(name)
                result.points += int(entry.get("point_count", 0) or 0)
                result.shards += len(entry.get("shards") or [])
                error = ""
                break
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if attempt >= RETRY_ATTEMPTS:
                    break
                delay = _backoff(attempt)
                if not budget.can_retry(estimate, delay):
                    # The sleep alone would eat the slot, and the re-export it
                    # starts cannot finish. Abandon the retry, keep the error.
                    error = f"{error} (retry abandoned: unaffordable in slot)"
                    logger.error(
                        "chunk_retry_abandoned collection=%s attempt=%d elapsed=%.1fmin"
                        " backoff=%.0fs est=%.1fmin err=%s", name, attempt + 1,
                        budget.elapsed() / 60, delay, estimate / 60, str(exc),
                    )
                    break
                logger.warning(
                    "chunk_retry collection=%s attempt=%d/%d in=%.1fs err=%s",
                    name, attempt + 1, RETRY_ATTEMPTS, delay, error,
                )
                time.sleep(delay)

        if error:
            result.failed[name] = error
            logger.error("chunk_gave_up collection=%s err=%s", name, error)

        # Flush after every collection: a kill can never lose the whole day.
        try:
            flush_fn(entries, result.failed)
        except Exception as exc:
            logger.error("manifest_flush_failed after=%s err=%s", name, exc)

    result.elapsed_s = budget.elapsed()
    return result
