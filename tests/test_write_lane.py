"""Write-lane selection: the vector's dim decides the lane, or we raise.

Covers the production defect (2560 vector -> 4096 twin -> dropped write) and
the inverse drift, because both are the same bug class with opposite signs.
"""

from __future__ import annotations

import pytest

from rugbrain.qdrant_store import write_lane
from rugbrain.qdrant_store.write_lane import (
    WriteLaneDimMismatch,
    write_lane_for_dim,
)


class TestProductionDefect:
    """The exact 2026-09-28 failure: 2560 vector, 4096 twin."""

    def test_2560_vector_lands_on_the_2560_base_not_the_4096_twin(self):
        """THE regression test.

        Resolver said ``_8b`` (4096); the vector is 2560-wide. The store then
        refused and the write vanished. The fix routes the 2560 vector to the
        retained 2560 base, which genuinely accepts it, instead of dropping it.
        """
        assert write_lane_for_dim("threat_intel_v2", 2560) == "threat_intel_v2"

    def test_4096_vector_is_the_only_thing_that_touches_the_twin(self):
        assert write_lane_for_dim("threat_intel_v2", 4096) == "threat_intel_v2_8b"

    def test_a_vector_of_a_third_width_still_fails_loud(self):
        """The guard is not a 'make it fit' resolver. 2048 fits NEITHER lane."""
        with pytest.raises(WriteLaneDimMismatch, match="write_lane_dim_mismatch"):
            write_lane_for_dim("threat_intel_v2", 2048)


class TestIdempotence:
    def test_already_resolved_twin_at_matching_dim_passes_through(self):
        assert write_lane_for_dim("threat_intel_v2_8b", 4096) == "threat_intel_v2_8b"

    def test_already_resolved_twin_with_2560_vector_re_homes_to_base(self):
        """Idempotence must not pin a mismatched lane; it re-decides on the dim."""
        assert write_lane_for_dim("threat_intel_v2_8b", 2560) == "threat_intel_v2"


class TestBaseEligibleNames:
    """Names with no twin: the base IS the lane, so it must be reachable."""

    def test_privacy_protocol_4096(self):
        assert write_lane_for_dim("privacy_protocol", 4096) == "privacy_protocol"

    def test_privacy_protocol_wrong_dim_raises(self):
        with pytest.raises(WriteLaneDimMismatch):
            write_lane_for_dim("privacy_protocol", 2560)

    def test_pinned_2560_name_accepts_2560(self):
        assert write_lane_for_dim("rug_events", 2560) == "rug_events"

    def test_pinned_1024_name_accepts_1024(self):
        assert write_lane_for_dim("wallet_clusters", 1024) == "wallet_clusters"


class TestUnclassifiedAndEmpty:
    def test_unclassified_name_defers_to_the_schema_check(self):
        """A collection this module has never heard of is NOT its business.

        Raising here broke legitimate writes: scratch collections, test
        collections and interim-suffixed names are all undeclared, and they
        were rejected outright. The guard exists to stop a KNOWN lane from
        receiving the wrong-width vector, not to invent policy for names it has
        no declaration for. The existing schema check in store_write is the
        right place to judge those, so the name is returned unchanged.
        """
        assert write_lane_for_dim("no_such_corpus", 4096) == "no_such_corpus"
        assert write_lane_for_dim("scratch_coll", 2560) == "scratch_coll"

    @pytest.mark.parametrize("name", [None, ""])
    def test_empty_collection_raises(self, name):
        with pytest.raises(WriteLaneDimMismatch, match="empty_collection"):
            write_lane_for_dim(name, 4096)


class TestDoesNotLieUnderRetiredEnv:
    """Regression on the root cause: the DECLARATION moved with the env var.

    ``CANONICAL_LANE_DIM`` is derived from ``COLLECTION_DIM``, which reads the
    same ``RAG_EMBED_DIM`` the embedder reads. Pinning the env to 2560 must not
    make this module claim a 2560 vector fits the 4096 twin.
    """

    def test_lane_dim_is_pinned_not_env_derived(self, monkeypatch):
        monkeypatch.setenv("RAG_EMBED_DIM", "2560")
        # Still refuses the 4096 twin for a 2560 vector: the twin's width is a
        # measured constant, not a re-read of the embedder's own configuration.
        assert write_lane_for_dim("threat_intel_v2", 2560) != "threat_intel_v2_8b"

    def test_4096_vector_still_allowed_under_the_2560_env(self, monkeypatch):
        """A correctly-configured 4096 embedder is not second-guessed by env."""
        monkeypatch.setenv("RAG_EMBED_DIM", "2560")
        assert write_lane_for_dim("threat_intel_v2", 4096) == "threat_intel_v2_8b"

    def test_wrong_dim_is_loud_even_with_resolver_disabled(self, monkeypatch):
        monkeypatch.setenv("RAG_LANE_RESOLVER", "off")
        with pytest.raises(WriteLaneDimMismatch):
            write_lane_for_dim("threat_intel_v2", 2048)


class TestDimIsAFact:
    def test_carries_requested_dim(self):
        with pytest.raises(WriteLaneDimMismatch) as exc:
            write_lane_for_dim("threat_intel_v2", 1234)
        assert exc.value.requested == 1234
        assert "threat_intel_v2_8b" in exc.value.candidates

    def test_module_is_importable_standalone(self):
        assert write_lane.write_lane_for_dim is write_lane_for_dim
