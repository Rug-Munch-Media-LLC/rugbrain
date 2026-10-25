"""Odds parser + staleness labelling (pure functions, no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugbrain.firehose.odds import STALE_AFTER_HOURS, parse_market

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _market(**over: object) -> dict:
    base = {
        "question": "Will SOL close above $200 on 2027-01-01?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.31", "0.69"]',
        "volumeNum": "1234567.5",
        "endDate": "2027-01-01T04:59:00Z",
        "updatedAt": (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "closed": False,
    }
    base.update(over)
    return base


def test_probabilities_volume_and_resolution() -> None:
    m = parse_market(_market(), now=NOW)
    assert m["implied_prob"] == {"Yes": 0.31, "No": 0.69}
    assert m["volume_usd"] == pytest.approx(1234567.5)
    assert m["end_date"] == "2027-01-01T04:59:00Z"
    assert m["freshness"] == "fresh"
    assert m["age_h"] == pytest.approx(1.0, abs=0.01)


def test_stale_boundary() -> None:
    edge = NOW - timedelta(hours=STALE_AFTER_HOURS)
    assert parse_market(_market(updatedAt=edge.strftime("%Y-%m-%dT%H:%M:%SZ")), now=NOW)["freshness"] == "fresh"
    over = NOW - timedelta(hours=STALE_AFTER_HOURS + 0.5)
    assert parse_market(_market(updatedAt=over.strftime("%Y-%m-%dT%H:%M:%SZ")), now=NOW)["freshness"] == "stale"


def test_missing_timestamp_is_stale_not_fresh() -> None:
    m = parse_market(_market(updatedAt=None), now=NOW)
    assert m["updated"] is None
    assert m["freshness"] == "stale"


def test_broken_payload_yields_nulls_not_fabricated_numbers() -> None:
    m = parse_market(_market(outcomePrices="not json", volumeNum="n/a"), now=NOW)
    assert m["implied_prob"] == {}
    assert m["volume_usd"] is None


def test_mismatched_outcome_lengths_are_dropped() -> None:
    m = parse_market(_market(outcomePrices='["0.5"]'), now=NOW)
    assert m["implied_prob"] == {}
