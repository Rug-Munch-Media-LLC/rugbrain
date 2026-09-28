"""Regression tests for the Telegram userbot sweep rotation (2026-09-28).

Covers the two defects found on the live account:

* ``run_cycle`` capped at ``RAG_TG_MAX_CHANNELS_PER_CYCLE`` (150) over 219
  dialogs with a *static* order → 69 channels permanently starved.
* ``state["channels"]`` was written only for channels that returned messages →
  154 of 219 enumerated channels tracked at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from rugbrain.ingest_sources.sweep_order import (
    CATEGORY_FAIRNESS,
    mark_swept,
    order_for_sweep,
    track_channels,
)

#: Mirrors the live fleet: 219 dialogs enumerated, 150 swept per cycle.
INVENTORY_SIZE = 300
BUDGET = 150


def make_inventory(n: int = INVENTORY_SIZE) -> list[dict[str, Any]]:
    """``n`` channels spread across every category the taxonomy emits."""
    cats = sorted(CATEGORY_FAIRNESS)
    return [
        {
            "id": 1000 + i,
            "title": f"Channel {i}",
            "username": f"channel{i}",
            "category": cats[i % len(cats)],
            "is_channel": True,
            "members": 100 + i,
        }
        for i in range(n)
    ]


def sweep_once(
    inventory: list[dict[str, Any]], state: dict[str, Any], budget: int, now: float
) -> list[int]:
    """One cycle: track → order → stamp. Returns the ids swept."""
    track_channels(state, inventory)
    ordered = order_for_sweep(inventory, state, budget)
    for chan in ordered:
        mark_swept(state, chan["id"], now=now)
    return [c["id"] for c in ordered]


def test_cap_is_a_budget_not_a_membership_filter() -> None:
    """A 300-channel fleet with a 150 budget sweeps 150 — and rotates, not repeats."""
    inventory = make_inventory()
    state: dict[str, Any] = {}
    t0 = 1_757_000_000.0

    first = sweep_once(inventory, state, BUDGET, t0)
    assert len(first) == BUDGET
    assert len(set(first)) == BUDGET  # no repeats inside one cycle

    second = sweep_once(inventory, state, BUDGET, t0 + 60)
    assert len(set(first) & set(second)) == 0, "same 150 channels re-swept = the bug"

    covered = set(first) | set(second)
    assert covered == {c["id"] for c in inventory}, "not every channel rotated in"


def test_every_channel_swept_within_three_cycles() -> None:
    """300 channels / 150 budget → full coverage in exactly 2 cycles."""
    inventory = make_inventory()
    state: dict[str, Any] = {}
    t0 = 1_757_000_000.0
    seen: set[int] = set()
    for i in range(2):
        seen |= set(sweep_once(inventory, state, BUDGET, t0 + 60 * i))
    assert seen == {c["id"] for c in inventory}


def test_channel_with_zero_messages_still_advances_last_swept() -> None:
    """A quiet channel must not be re-fetched first on every cycle."""
    inventory = make_inventory(10)
    state: dict[str, Any] = {"channels": {}}
    t0 = 1_757_000_000.0
    quiet = inventory[0]["id"]

    mark_swept(state, quiet, new_msg_id=0, now=t0)
    entry = state["channels"][str(quiet)]
    assert entry["last_swept"] is not None
    assert entry["last_msg_id"] == 0, "zero messages must not move the watermark"

    # The other 9 are still never-swept, so they all sort ahead of the quiet one.
    ahead = order_for_sweep(inventory, state, 9)
    assert quiet not in [c["id"] for c in ahead]
    assert order_for_sweep(inventory, state, 10)[-1]["id"] == quiet


def test_last_msg_id_never_moves_backwards() -> None:
    """The watermark only advances; a lower msg_id cannot rewind it."""
    state: dict[str, Any] = {"channels": {}}
    mark_swept(state, 42, new_msg_id=900, now=1_757_000_000.0)
    mark_swept(state, 42, new_msg_id=500, now=1_757_000_100.0)
    assert state["channels"]["42"]["last_msg_id"] == 900
    mark_swept(state, 42, new_msg_id=901, now=1_757_000_200.0)
    assert state["channels"]["42"]["last_msg_id"] == 901


def test_track_channels_reconciles_to_full_inventory() -> None:
    """Quiet channels are tracked too — state converges on the inventory."""
    inventory = make_inventory(219)
    state: dict[str, Any] = {"channels": {"1000": {"last_msg_id": 7, "last_swept": None}}}
    assert track_channels(state, inventory) == len(inventory) - 1
    assert set(state["channels"]) == {str(c["id"]) for c in inventory}
    # Existing watermark + meta preserved, stamps defaulted in place.
    assert state["channels"]["1000"]["last_msg_id"] == 7
    assert state["channels"]["1000"]["last_swept"] is None
    # Idempotent: a second pass adds nothing.
    assert track_channels(state, inventory) == 0


def test_category_fairness_only_breaks_exact_staleness_ties() -> None:
    """Staleness dominates; the fairness rank only orders equal staleness."""
    now = datetime.now(UTC)
    inventory = [
        {"id": 1, "title": "m", "username": "m", "category": "general"},
        {"id": 2, "title": "s", "username": "s", "category": "scam_caller"},
        {"id": 3, "title": "n", "username": "n", "category": "news"},
    ]
    # channel 1 was swept long ago; 2 and 3 never. Never-swept must win.
    state: dict[str, Any] = {
        "channels": {
            "1": {
                "last_msg_id": 0,
                "last_swept": (now - timedelta(hours=6)).isoformat(),
            }
        }
    }
    assert [c["id"] for c in order_for_sweep(inventory, state, 3)] == [2, 3, 1]

    # All equally stale → category rank decides, and no category monopolises.
    for chan in inventory:
        state["channels"][str(chan["id"])] = {"last_msg_id": 0, "last_swept": now.isoformat()}
    assert [c["id"] for c in order_for_sweep(inventory, state, 3)] == [2, 3, 1]
    # Budget of 1 always yields the top-priority category while ties persist.
    assert [c["id"] for c in order_for_sweep(inventory, state, 1)] == [2]


def test_zero_or_negative_budget_sweeps_everything() -> None:
    inventory = make_inventory(5)
    state: dict[str, Any] = {}
    assert len(order_for_sweep(inventory, state, 0)) == 5
    assert len(order_for_sweep(inventory, state, -1)) == 5


def test_corrupt_last_swept_is_treated_as_never_swept() -> None:
    inventory = make_inventory(3)
    state: dict[str, Any] = {"channels": {"1000": {"last_msg_id": 0, "last_swept": "not-a-date"}}}
    ordered = [c["id"] for c in order_for_sweep(inventory, state, 3)]
    assert ordered[0] == 1000, "unparseable stamp must not hide a channel"


@pytest.mark.parametrize(
    ("max_channels", "expected"),
    [(0, 300), (150, 150), (299, 299), (300, 300), (500, 300)],
)
def test_budget_edge_cases(max_channels: int, expected: int) -> None:
    inventory = make_inventory()
    state: dict[str, Any] = {}
    assert len(order_for_sweep(inventory, state, max_channels)) == expected
