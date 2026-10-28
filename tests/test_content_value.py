"""Value score: explainable, monotone, and stable to the last decimal.

The score is the input to the publication gate, so the contract under test is
the ARITHMETIC: every component's contribution must equal raw x weight, the
weights must sum to 1, the value must be the sum minus the dedupe penalty, and
the same input must produce the same breakdown every time.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rugbrain import content_value_components as parts
from rugbrain.content_value import DEDUPE_WEIGHT, WEIGHTS, score_candidate

FIXTURE = Path(__file__).parent / "fixtures" / "content" / "value_fixtures.json"
BY_ID = {item["id"]: item for item in json.loads(FIXTURE.read_text())["items"]}

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)


def test_weights_sum_to_one() -> None:
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_breakdown_is_self_consistent() -> None:
    scored = score_candidate(BY_ID["kelp-tg"], now=NOW)
    breakdown = scored["value_breakdown"]
    assert set(breakdown["components"]) == set(WEIGHTS)
    for name, item in breakdown["components"].items():
        assert item["weight"] == WEIGHTS[name]
        assert item["contribution"] == round(item["raw"] * item["weight"], 4)
    positive = round(sum(i["contribution"] for i in breakdown["components"].values()), 4)
    penalty = breakdown["dedupe_penalty"]
    assert penalty["contribution"] == -round(penalty["raw"] * DEDUPE_WEIGHT, 4)
    assert scored["value"] == round(min(1.0, max(0.0, positive + penalty["contribution"])), 4)


def test_score_is_stable_for_the_same_input() -> None:
    first = score_candidate(BY_ID["whale-move"], now=NOW)
    second = score_candidate(BY_ID["whale-move"], now=NOW)
    assert first == second
    assert first["value"] > 0.0


def test_duplicate_scores_below_a_novel_story() -> None:
    fresh = score_candidate(BY_ID["eth-pq"], now=NOW)
    duplicate = score_candidate(
        BY_ID["eth-pq"],
        now=NOW,
        evidence={"exact": True, "jaccard": 1.0, "cosine": None, "similarity": 1.0},
    )
    assert duplicate["value"] < fresh["value"]
    assert duplicate["value_breakdown"]["dedupe_penalty"]["raw"] == 1.0
    assert duplicate["value_breakdown"]["components"]["novelty"]["raw"] == 0.0


def test_higher_similarity_lowers_value_monotonically() -> None:
    values = [
        score_candidate(
            BY_ID["eth-pq"],
            now=NOW,
            evidence={"exact": False, "jaccard": 0.0, "cosine": sim, "similarity": sim},
        )["value"]
        for sim in (0.0, 0.3, 0.55, 0.62, 0.9)
    ]
    assert values == sorted(values, reverse=True)
    assert len(set(values)) == len(values)


def test_onchain_magnitude_scale() -> None:
    assert parts.onchain_magnitude({"pnl_usd": 1_000_000}) == pytest.approx(0.6)
    assert parts.onchain_magnitude({"volume_usd": 1_000_000_000}) == pytest.approx(0.9)
    assert parts.onchain_magnitude({"whale": True}) == parts.WHALE_ONLY_MAGNITUDE
    assert parts.onchain_magnitude({}) == 0.0


def test_recency_half_life_and_undated() -> None:
    assert parts.recency(0) == 1.0
    assert parts.recency(12) == pytest.approx(0.5)
    assert parts.recency(24) == pytest.approx(0.25)
    assert parts.recency(None) == 0.0  # undated is never assumed fresh
    story = dict(BY_ID["eth-pq"])
    story["ts"] = ""
    assert score_candidate(story, now=NOW)["value_breakdown"]["components"]["recency"]["raw"] == 0.0


def test_undated_story_scores_below_the_same_story_dated() -> None:
    dated = score_candidate(BY_ID["btc-funding"], now=NOW)["value"]
    copy = dict(BY_ID["btc-funding"])
    copy["ts"] = "not-a-date"
    assert score_candidate(copy, now=NOW)["value"] < dated


def test_source_diversity_saturates_at_four() -> None:
    assert parts.source_diversity(1) == 0.0
    assert parts.source_diversity(2) == pytest.approx(0.5)
    assert parts.source_diversity(4) == 1.0
    assert parts.source_diversity(9) == 1.0


def test_corroboration_raises_the_score() -> None:
    alone = score_candidate(BY_ID["sol-outage"], now=NOW)["value"]
    corroborated = dict(BY_ID["sol-outage"])
    corroborated["also_seen_in"] = ["x:one", "rss:two"]
    assert score_candidate(corroborated, now=NOW)["value"] > alone


def test_severity_escalates_the_reader_prior() -> None:
    assert parts.reader_interest_prior("scam", "critical") > parts.reader_interest_prior(
        "scam", "low"
    )
    assert parts.reader_interest_prior("market", "critical") < parts.reader_interest_prior(
        "scam", "critical"
    )


def test_value_is_bounded() -> None:
    for item in BY_ID.values():
        value = score_candidate(item, now=NOW)["value"]
        assert 0.0 <= value <= 1.0


def test_age_is_measured_from_ts_not_now() -> None:
    story = dict(BY_ID["eth-pq"])
    story["ts"] = (NOW - timedelta(hours=12)).isoformat()
    assert score_candidate(story, now=NOW)["value_breakdown"]["components"]["recency"][
        "raw"
    ] == pytest.approx(0.5)
