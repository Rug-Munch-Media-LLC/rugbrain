"""SPDX-License-Identifier: MIT

Copyright (c) 2026 Rug Munch Media LLC

Deterministic selection: collapse, score, route, take the edition's top items.

Two independent suppression paths, and both are counted because they fail
differently:

*   ``dedupe`` collapses copies that arrived in *this* pool (same story via
    Telegram, X and RSS).
*   ``suppressed`` refs come from the ledger — stories already attached to a
    published edition in the last two weeks. A story may legitimately appear
    in two editions on one day; it may not anchor both.

Routing is Lane C's ladder; this module only asks it and drops ``feed``/``skip``
(it never decides a threshold).
"""

from __future__ import annotations

from typing import Any

from rugbrain.content_daily import editions
from rugbrain.content_daily.ports import DedupeFn, RouteFn, ScoreFn

#: Routes that put an item in front of a reader now; ``feed`` is archive-only.
BROADCAST_ROUTES = frozenset({"channel", "both"})


def score_and_dedupe(
    stories: list[dict[str, Any]],
    *,
    dedupe: DedupeFn,
    score: ScoreFn,
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(scored pool, dedupe drops)``. Drops are copies, not failures."""
    pool = dedupe(stories)
    drops = max(0, len(stories) - len(pool))
    return [dict(story, score=score(story)) for story in pool], drops


def select(
    pool: list[dict[str, Any]],
    *,
    edition: editions.Edition,
    route: RouteFn,
    suppressed: set[str],
    day: str,
) -> tuple[list[dict[str, Any]], int]:
    """Route the pool, drop already-published refs, take the top items."""
    chosen: list[dict[str, Any]] = []
    dropped = 0
    for story in pool:
        score = float(story.get("score") or 0.0)
        ref = str(story.get("source") or "")
        duplicate = bool(ref) and ref in suppressed
        decision = route(
            score, str(story.get("category") or ""), str(story.get("kind") or "story"), duplicate
        )
        if duplicate or decision.get("route") not in BROADCAST_ROUTES:
            dropped += 1
            continue
        chosen.append({**story, "score": score, "route": decision.get("route"), "day": day})
    chosen.sort(key=lambda item: item["score"], reverse=True)
    return chosen[: edition.max_items], dropped


def immediate(value: Any) -> Any:
    """Adapt a synchronous value into the awaitable the stage runner expects."""

    async def _value() -> Any:
        return value

    return _value


__all__ = ["BROADCAST_ROUTES", "immediate", "score_and_dedupe", "select"]
