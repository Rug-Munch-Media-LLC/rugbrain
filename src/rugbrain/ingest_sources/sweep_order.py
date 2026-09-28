"""Staleness-ordered channel sweep (2026-09-28).

The bug: ``run_cycle`` ordered channels round-robin by index across categories
and then truncated at ``RAG_TG_MAX_CHANNELS_PER_CYCLE``. With 219 live dialogs
and a 150 budget, the same ~150 channels were scraped every cycle and **69 were
permanently starved** — permanently, not intermittently.

The fix is a *rotating cursor*, not a bigger cap:

* ``state["channels"][id]["last_swept"]`` (UTC ISO) is stamped for **every**
  channel the cycle actually swept, including ones that returned zero messages
  (previously only channels that produced messages were written at all, so
  ``state`` tracked 154 of 219 known channels).
* Ordering is strictly by staleness: never-swept first, then oldest
  ``last_swept``. ``CATEGORY_FAIRNESS`` only breaks *exact* staleness ties, so
  one category can never monopolise the budget and one starve another.

``last_msg_id`` keeps its existing watermark semantics untouched — it only ever
moves forward, and only when the channel actually returned messages.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

#: Soft preference for *staleness ties only*. Lower rank = swept first.
CATEGORY_FAIRNESS: dict[str, int] = {
    "scam_caller": 0,
    "security": 1,
    "shiller": 2,
    "news": 3,
    "data": 4,
    "market": 5,
    "general": 6,
}
_UNRANKED = 99


def _epoch(value: Any) -> float:
    """UTC epoch of an ISO timestamp; 0.0 for missing/unparseable (never swept)."""
    if not value:
        return 0.0
    try:
        stamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return 0.0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.timestamp()


def last_swept_epoch(entry: dict[str, Any] | None) -> float:
    """Staleness of one ``state["channels"][id]`` record (0.0 = never swept)."""
    return _epoch((entry or {}).get("last_swept"))


def track_channels(state: dict[str, Any], inventory: list[dict[str, Any]]) -> int:
    """Reconcile ``state["channels"]`` with the full inventory. Returns # added.

    Every enumerated channel gets a record immediately, so the tracked set
    converges on the inventory instead of trailing it by however many channels
    happened to be quiet last cycle.
    """
    channels = state.setdefault("channels", {})
    added = 0
    for chan in inventory:
        key = str(chan["id"])
        entry = channels.get(key)
        if not isinstance(entry, dict):
            channels[key] = {"last_msg_id": 0, "last_swept": None}
            entry = channels[key]
            added += 1
        entry.setdefault("last_msg_id", 0)
        entry.setdefault("last_swept", None)
        entry["title"] = chan["title"]
        entry["category"] = chan["category"]
    return added


def order_for_sweep(
    inventory: list[dict[str, Any]], state: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    """Stalest-first sweep order, truncated to this cycle's budget.

    Primary key is ``last_swept`` (0.0 = never swept), so every channel rotates
    through the budget whatever the cap. Category rank then id only ever break
    exact ties between equally stale channels.
    """
    channels = state.get("channels") or {}

    def rank(chan: dict[str, Any]) -> tuple[float, int, str]:
        stale = last_swept_epoch(channels.get(str(chan["id"])))
        fair = CATEGORY_FAIRNESS.get(str(chan.get("category", "")), _UNRANKED)
        return (stale, fair, str(chan["id"]))

    ordered = sorted(inventory, key=rank)
    return ordered if limit <= 0 else ordered[:limit]


def mark_swept(
    state: dict[str, Any], channel_id: Any, new_msg_id: int = 0, now: float | None = None
) -> None:
    """Record that a channel was swept this cycle, advancing both cursors.

    Called for every swept channel — a channel that returned zero messages
    still advances ``last_swept`` (otherwise it is re-fetched first, forever),
    while ``last_msg_id`` only moves forward when there were messages.
    """
    entry = state.setdefault("channels", {}).setdefault(
        str(channel_id), {"last_msg_id": 0, "last_swept": None}
    )
    if new_msg_id > int(entry.get("last_msg_id") or 0):
        entry["last_msg_id"] = new_msg_id
    stamp = datetime.fromtimestamp(time.time() if now is None else now, tz=UTC)
    entry["last_swept"] = stamp.isoformat()


__all__ = [
    "CATEGORY_FAIRNESS",
    "last_swept_epoch",
    "mark_swept",
    "order_for_sweep",
    "track_channels",
]
