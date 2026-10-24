"""The publication gate is one decision with explicit thresholds.

Contract under test (``src/rugbrain/content_gate.py``): the route depends on
score, category, kind and duplicate status — never on which source family the
item arrived through. Every assertion checks the ROUTE, and the reason where
the reason is the thing that keeps the decision auditable.
"""

from __future__ import annotations

import pytest

from rugbrain.content_gate import (
    BOTH_MIN,
    CHANNEL_MIN,
    SCORE_FLOOR,
    SECURITY_BOTH_MIN,
    SECURITY_CHANNEL_MIN,
    decide,
)


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.95, "both"),  # high
        (0.70, "channel"),  # medium
        (0.50, "feed"),  # low-but-publishable
        (0.20, "skip"),  # below floor
    ],
)
def test_editorial_ladder_high_medium_low(score: float, expected: str) -> None:
    assert decide(score, "news", "story", False)["route"] == expected


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (BOTH_MIN, "both"),
        (BOTH_MIN - 0.001, "channel"),
        (CHANNEL_MIN, "channel"),
        (CHANNEL_MIN - 0.001, "feed"),
        (SCORE_FLOOR, "feed"),
        (SCORE_FLOOR - 0.001, "skip"),
    ],
)
def test_thresholds_are_inclusive_at_the_boundary(score: float, expected: str) -> None:
    assert decide(score, "research", "story", False)["route"] == expected


@pytest.mark.parametrize("score", [0.95, 0.70, 0.50])
def test_duplicate_never_rebroadcasts(score: float) -> None:
    result = decide(score, "news", "story", True)
    assert result["route"] == "feed"
    assert "no re-post" in result["reason"]


def test_stale_duplicate_is_skipped() -> None:
    assert decide(0.10, "news", "story", True)["route"] == "skip"


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.80, "both"),
        (0.50, "channel"),  # escalates below the editorial channel bar
        (0.40, "feed"),
        (0.10, "skip"),
    ],
)
def test_scam_and_security_escalate(score: float, expected: str) -> None:
    assert decide(score, "scam", "story", False)["route"] == expected
    assert decide(score, "security", "story", False)["route"] == expected


def test_security_escalation_is_category_only_not_source() -> None:
    """Same score/category/kind routes identically from any source family."""
    routes = {
        decide(0.50, category, "story", False)["route"]
        for category in ("news", "research", "x_social", "social")
    }
    assert routes == {"feed"}  # 0.50 is above the floor, below every channel bar


def test_security_medium_is_channel_where_editorial_medium_is_not() -> None:
    assert decide(0.50, "security", "story", False)["route"] == "channel"
    assert decide(0.50, "news", "story", False)["route"] == "feed"
    assert SECURITY_CHANNEL_MIN < CHANNEL_MIN
    assert SECURITY_BOTH_MIN < BOTH_MIN


@pytest.mark.parametrize("score", [0.95, 0.70, 0.50])
def test_unknown_category_is_feed_only(score: float) -> None:
    result = decide(score, "unclassified-wire", "story", False)
    assert result["route"] == "feed"
    assert "unknown category" in result["reason"]
    assert "feed-only" in result["reason"]


def test_empty_category_is_unknown_not_a_crash() -> None:
    assert decide(0.90, "", "", False)["route"] == "feed"


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.95, "channel"),  # operational: channel, never mirrored
        (0.70, "channel"),
        (0.50, "skip"),  # no feed home, below channel bar
    ],
)
def test_operational_kinds_have_no_feed_home(score: float, expected: str) -> None:
    for kind in ("scan", "alert"):
        assert decide(score, "news", kind, False)["route"] == expected


def test_operational_security_uses_the_security_bar() -> None:
    result = decide(0.50, "security", "alert", False)
    assert result["route"] == "channel"
    assert "never mirrored" in result["reason"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_score_is_rejected_fail_closed(bad: float) -> None:
    assert decide(bad, "news", "story", False)["route"] == "skip"


def test_route_and_reason_are_always_present() -> None:
    for route_input in (
        (0.95, "news", "story", False),
        (0.10, "news", "story", False),
        (0.70, "news", "story", True),
    ):
        result = decide(*route_input)
        assert set(result) == {"route", "reason"}
        assert result["route"] in {"skip", "feed", "channel", "both"}
        assert result["reason"]
