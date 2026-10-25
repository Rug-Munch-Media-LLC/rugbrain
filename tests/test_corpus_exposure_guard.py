"""COVERAGE GUARD — a collection with >1,000 points and no door FAILS the suite.

The rule the operator asked for: corpus we already paid for may not sit
unreachable without the build saying so out loud. A warning would be ignored;
this fails.
"""

from __future__ import annotations

import pytest

from rugbrain.corpus_exposure import (
    EXPOSED,
    MIN_EXPOSED_POINTS,
    EmptyCorpus,
    audit_exposure,
    servable_lane,
)

#: Live Qdrant point counts, 2026-10-25 (rmi-qdrant-v2 :6334). Trimmed to the
#: collections the guard must judge; the live probe below re-checks the top ones.
LIVE_COUNTS = {
    "known_scams_8b": 27300,
    "defi_exploits_8b": 16713,
    "security_patterns_8b": 14275,
    "live_threats_8b": 11164,
    "scam_patterns_8b": 8080,
    "contract_audits_8b": 1050,
    "market_data": 1062,
    "exploits_8b": 1344,
    "solidity_patterns": 15,          # seed collection, below the floor
    "dedup_slice_test": 316,          # fixture, below the floor
    "live_threats": 0,                # dead base lane
}


class TestNoSilentCorpus:
    def test_every_large_collection_has_a_door(self):
        """THE GUARD. Adding a >1,000-point collection without exposure fails here."""
        report = audit_exposure(LIVE_COUNTS)
        assert report.gaps == (), (
            "unreachable corpus (add to corpus_exposure.EXPOSED or retire it): "
            + ", ".join(report.gaps)
        )

    def test_small_or_fixture_collections_are_not_flagged(self):
        report = audit_exposure({"solidity_patterns": 15, "dedup_slice_test": 316})
        assert report.gaps == ()

    def test_a_large_undeclared_collection_IS_flagged(self):
        """Proves the guard bites — not a tautology."""
        report = audit_exposure({"brand_new_8b": 5000})
        assert report.gaps == ("brand_new_8b(5000)",)

    def test_floor_is_inclusive_above_one_thousand(self):
        assert audit_exposure({"x": MIN_EXPOSED_POINTS}).gaps == ()
        assert audit_exposure({"x": MIN_EXPOSED_POINTS + 1}).gaps == ("x(1001)",)

    def test_reachability_is_point_weighted(self):
        report = audit_exposure({"known_scams_8b": 9000, "orphan_8b": 1000})
        assert report.reachable_points == 9000
        assert report.total_points == 10000
        assert report.pct_reachable == 90.0


class TestTypedRefusal:
    def test_dead_base_lane_resolves_to_its_twin(self):
        assert servable_lane("live_threats", LIVE_COUNTS) == "live_threats_8b"

    def test_lane_with_no_points_refuses_typed(self):
        """Empty must never be indistinguishable from 'no match'."""
        with pytest.raises(EmptyCorpus) as err:
            servable_lane("ghost_8b", LIVE_COUNTS)
        assert "empty_collection:ghost_8b" in str(err.value)

    def test_live_collection_passes_through(self):
        assert servable_lane("security_patterns_8b", LIVE_COUNTS) == "security_patterns_8b"


def test_registry_has_no_runtime_holes():
    """Every EXPOSED entry states a mechanism, so no empty claim passes review."""
    for name, token in EXPOSED.items():
        assert token.strip(), f"{name} has an empty exposure token"
