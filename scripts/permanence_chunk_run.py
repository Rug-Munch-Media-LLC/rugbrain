#!/usr/bin/env python3
"""Execute ONE time-bounded chunk of the permanence run, resumably.

Three invariants this module exists to hold:

1. **A chunk cannot approach the unit timeout.** The deadline is monotonic and
   is checked *before* each collection starts, so the process exits cleanly
   (and systemd never has to SIGTERM it). One collection may overrun the
   deadline -- it is already in flight, and killing it wastes the whole export
   -- but the next chunk boundary is never crossed by more than that single
   collection.

2. **Progress survives a kill.** The manifest is flushed after EVERY collection
   (success or failure), not once at the end. That is the actual root cause of
   2026-10-21/22/23: objects landed in B2 (51, 77, 86 of them) but no
   manifest.json, because `upload_manifest` ran only after the loop and the
   loop never finished. A day with objects and no manifest is unreadable.

3. **A failure is recorded, not swallowed.** 2026-10-23 05:50 lost
   `rag_documents_8b` to `503 service_unavailable: no tomes available` after
   b2sdk's own 5 internal tries. That failure existed only as a log line; the
   manifest had no way to say "85 expected, 84 landed". Failures now go into
   `missing[]` with the error, and the chunk continues instead of aborting.

One primary function: `run_chunk`.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger("permanence_chunk")

# Retries are for TRANSIENT faults only (503 no-tomes, connection reset,
# read timeout). b2sdk already retries uploads 5x internally, so this is the
# layer above it: 3 more attempts with real backoff, capped at 60s each.
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
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failed and not self.deferred

    def summary(self) -> str:
        return (
            f"done={len(self.done)} failed={len(self.failed)} "
            f"deferred={len(self.deferred)} points={self.points} "
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
) -> ChunkResult:
    """Snapshot *collections* until the budget is spent, flushing as it goes.

    ``snapshot_fn(name)`` must return one manifest entry (and must be safe to
    call again for the same collection: it overwrites the same object key).
    ``flush_fn(entries, failures)`` must merge-publish the day's manifest; it is
    called after every collection so a SIGTERM costs at most one collection.
    """
    result = ChunkResult()
    entries: list[dict] = []
    started = time.monotonic()

    for position, name in enumerate(collections):
        spent = time.monotonic() - started
        if spent >= budget_seconds:
            result.deferred = collections[position:]
            logger.warning(
                "chunk_budget_exhausted spent=%.1fmin budget=%.1fmin deferred=%d %s",
                spent / 60,
                budget_seconds / 60,
                len(result.deferred),
                ",".join(result.deferred[:10]),
            )
            break

        logger.info(
            "chunk_item %d/%d collection=%s spent=%.1fmin",
            position + 1,
            len(collections),
            name,
            spent / 60,
        )
        error = ""
        for attempt in range(RETRY_ATTEMPTS + 1):
            try:
                entry = snapshot_fn(name)
                entries.append(entry)
                result.done.append(name)
                result.points += int(entry.get("point_count", 0) or 0)
                error = ""
                break
            except Exception as exc:  # noqa: BLE001 - classified by retry policy
                error = f"{type(exc).__name__}: {exc}"
                if attempt < RETRY_ATTEMPTS:
                    delay = _backoff(attempt)
                    logger.warning(
                        "chunk_retry collection=%s attempt=%d/%d in=%.1fs err=%s",
                        name,
                        attempt + 1,
                        RETRY_ATTEMPTS,
                        delay,
                        error,
                    )
                    time.sleep(delay)

        if error:
            result.failed[name] = error
            logger.error("chunk_gave_up collection=%s err=%s", name, error)

        # Flush after every collection: a kill can never lose the whole day.
        try:
            flush_fn(entries, result.failed)
        except Exception as exc:  # noqa: BLE001
            logger.error("manifest_flush_failed after=%s err=%s", name, exc)

    result.elapsed_s = time.monotonic() - started
    return result
