"""Routing: tiers, daily caps, and the hand-off to the publication gate.

Routing decides WHERE; ``content_gate`` decides WHETHER. These tests pin both
halves of that contract and the one thing that must never drift: a duplicate is
never broadcast, whichever tier would have claimed it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rugbrain.content_gate import decide
from rugbrain.content_history import SeenLedger
from rugbrain.content_routing import CHANNELS, TIERS, route_item

FIXTURE = Path(__file__).parent / "fixtures" / "content" / "value_fixtures.json"
BY_ID = {item["id"]: item for item in json.loads(FIXTURE.read_text())["items"]}
DAY = "2026-10-20"


def route(item_id: str, value: float, *, seen: SeenLedger | None = None, duplicate: bool = False):
    return route_item(
        BY_ID[item_id], day=DAY, seen=seen or SeenLedger(), value=value, duplicate=duplicate
    )


def test_table_is_well_formed() -> None:
    assert [tier.name for tier in TIERS] == [
        "urgent",
        "lead",
        "whales",
        "alpha",
        "news",
        "site",
    ]
    assert all(tier.channel in CHANNELS for tier in TIERS)
    assert all(tier.max_per_day > 0 for tier in TIERS)
    # Precedence is non-increasing: specific tiers (whales, alpha) share a bar
    # and are ordered by how narrow their rule is, never above the lead tier.
    thresholds = [tier.min_value for tier in TIERS]
    assert thresholds == sorted(thresholds, reverse=True)
    assert len({tier.channel for tier in TIERS}) == len(TIERS)


def test_big_story_goes_to_alerts_and_main() -> None:
    decision = route("kelp-tg", 0.92)
    assert decision["tier"] == "urgent"
    assert decision["channel"] == "alerts"
    assert decision["mirror"] == "main"
    assert decision["route"] == "both"
    assert "gate" in decision["reason"]


def test_lead_story_goes_to_main_plus_site_edition() -> None:
    decision = route("btc-funding", 0.74)
    assert decision["tier"] == "lead"
    assert decision["channel"] == "main"
    assert decision["daily_edition"] is True
    # mirror is the GATE's feed mirror: a channel-only item is not mirrored.
    assert decision["mirror"] == ""


def test_daily_cap_falls_through_not_dropped() -> None:
    seen = SeenLedger()
    for _ in range(2):
        seen.remember(BY_ID["kelp-tg"], channel="alerts", day=DAY)
    decision = route("kelp-tg", 0.92, seen=seen)
    assert decision["tier"] == "lead"
    assert decision["channel"] == "main"
    assert "urgent full (2/2)" in decision["reason"]


def test_all_caps_full_is_a_hold_with_the_reason() -> None:
    seen = SeenLedger()
    for tier in TIERS:
        for _ in range(tier.max_per_day):
            seen.remember(BY_ID["eth-pq"], channel=tier.channel, day=DAY)
    decision = route("eth-pq", 0.68, seen=seen)
    assert decision["route"] == "skip"
    assert "all eligible tiers full" in decision["reason"]


def test_gate_skip_is_never_overridden() -> None:
    decision = route("eth-pq", 0.20)
    assert decision["route"] == "skip"
    assert decision["gate_route"] == "skip"
    assert decision["channel"] == ""


def test_gate_feed_wins_over_a_broadcast_tier() -> None:
    """An unclassified story is archived even when its value clears main."""
    story = dict(BY_ID["eth-pq"])
    story["category"] = "unclassified"
    decision = route_item(story, day=DAY, seen=SeenLedger(), value=0.95)
    assert decision["gate_route"] == "feed"
    assert decision["channel"] == "site"
    assert "archive" in decision["reason"]


def test_duplicate_never_rebroadcasts() -> None:
    decision = route("kelp-tg", 0.99, duplicate=True)
    assert decision["route"] == "feed"
    assert decision["channel"] == "site"
    assert "duplicate" in decision["gate_reason"]


def test_alpha_requires_a_depth_category() -> None:
    research = route("eth-pq", 0.65)
    assert research["tier"] == "alpha"
    assert research["channel"] == "alpha"
    market = route("btc-funding", 0.65)
    assert market["tier"] == "news"


def test_whales_requires_onchain_magnitude() -> None:
    whale = route("whale-move", 0.69)
    assert whale["tier"] == "whales"
    assert whale["channel"] == "whales"
    no_figure = dict(BY_ID["whale-move"])
    no_figure["metadata"] = {}
    # No figure: the on-chain depth channel is still the right home.
    decision = route_item(no_figure, day=DAY, seen=SeenLedger(), value=0.69)
    assert decision["tier"] == "alpha"


def test_routing_calls_the_gate_rather_than_reimplementing_it() -> None:
    """Same inputs, same answer: routing may not disagree with content_gate."""
    for value in (0.2, 0.4, 0.5, 0.65, 0.75, 0.9):
        decision = route("kelp-tg", value)
        assert decision["gate_route"] == decide(value, "scam", "story", False)["route"]


@pytest.mark.parametrize("item_id", sorted(BY_ID))
def test_every_item_gets_a_reason(item_id: str) -> None:
    decision = route(item_id, 0.6)
    assert decision["reason"]
    assert decision["route"] in ("skip", "feed", "channel", "both")
