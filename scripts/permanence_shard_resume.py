#!/usr/bin/env python3
"""Read a prior manifest entry as shard resume state.

A collection is "done" only when its manifest entry is complete. An entry with
``partial: true`` is a collection the writer got partway through: its ``shards``
list is already in B2 and its last shard carries the ``resume_cursor`` the
scroll must restart from.

Why this module exists: resume used to be per COLLECTION (``todays_done_
collections``), so a kill inside ``rag_documents_8b`` resumed at collection 0 of
that collection -- part-00000 uploaded again, 59 minutes spent twice. Measured
2026-10-23: part 0 = 5,471 MB in ~59 min against an 80-min chunk, so the day
never advanced at all. Per-shard resume is what makes the day fit.

One primary function: ``resume_state``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("permanence_shard_resume")

# A partial collection whose cursor is gone cannot be resumed safely: replaying
# from the start would duplicate points into new shards.
_EMPTY: dict[str, Any] = {"shards": [], "cursor": None, "points_done": 0}


def entry_is_done(entry: dict[str, Any] | None) -> bool:
    """True when this manifest entry needs no further work.

    `partial` is not the final word. A sharded collection stopped by the chunk
    budget is partial BY DESIGN, and it is finished the moment its LAST shard
    lands — the shard list only ever names shards already uploaded to B2.
    Judging on the flag alone is what made a 1,136,363-point collection whose
    23 shards had all landed still read "partial" forever: the day could never
    complete, and the same collection was re-entered every firing.
    """
    if not entry:
        return False
    if not entry.get("partial"):
        return True
    expected = int(entry.get("expected_shards") or 0)
    shards = entry.get("shards") or []
    return expected > 0 and len(shards) >= expected


def resume_state(entry: dict[str, Any] | None) -> dict[str, Any]:
    """Resume a half-written collection from its own recorded shards.

    Returns ``{"shards": [...], "cursor": <point id>|None, "points_done": N}``.
    ``cursor`` None means the prior shards exist but no scroll position was
    recorded, so the collection is re-exported from the start (the old
    behaviour) rather than guessed at.
    """
    if not entry or entry_is_done(entry):
        return dict(_EMPTY)
    shards = [s for s in (entry.get("shards") or []) if isinstance(s, dict)]
    if not shards:
        return dict(_EMPTY)
    cursor = shards[-1].get("resume_cursor")
    if cursor is None:
        logger.warning(
            "resume_cursor_absent collection=%s shards=%d -> restarting",
            entry.get("collection"),
            len(shards),
        )
        return dict(_EMPTY)
    return {
        "shards": shards,
        "cursor": cursor,
        "points_done": sum(int(s.get("point_count", 0) or 0) for s in shards),
    }
