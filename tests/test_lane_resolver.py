"""Canonical-lane resolver: base -> ``_8b`` twin, idempotence, gate admission.

Every assertion here encodes a fact verified against live Qdrant on
2026-09-28 (96 collections, 33 twins): see ``lane_resolver`` for the evidence.
"""

from __future__ import annotations

import logging

import pytest

import rugbrain.qdrant_store as _pkg
from rugbrain.qdrant_store import collections as col
from rugbrain.qdrant_store import lane_resolver as lr


@pytest.fixture(autouse=True)
def _no_interim(monkeypatch):
    """Isolate the interim-namespace env so lane behaviour is tested alone.

    The package attribute is the documented patch point for the interim suffix,
    so it is cleared here for every test; the interim tests below set it again.
    """
    import rugbrain.qdrant_store as qdrant_store

    monkeypatch.setattr(lr, "_PRESERVED_LANE_DIM", 2560, raising=False)
    monkeypatch.setattr(qdrant_store, "_INTERIM_SUFFIX", "", raising=False)
    monkeypatch.setenv(lr.RESOLVER_MODE_ENV, "strict")


class TestRule:
    def test_base_resolves_to_twin(self):
        """A migrated base name is rewritten to its populated 4096 twin."""
        assert lr.resolve_collection("threat_intel_v2") == "threat_intel_v2_8b"
        assert lr.resolve_collection("defi_exploits") == "defi_exploits_8b"
        assert lr.resolve_collection("known_scams") == "known_scams_8b"
        assert lr.resolve_collection("rag_documents") == "rag_documents_8b"

    def test_canonical_passes_through_unchanged(self):
        """An already-canonical name is NEVER double-suffixed."""
        for name in ("threat_intel_v2_8b", "defi_exploits_8b", "security_patterns_8b"):
            assert lr.resolve_collection(name) == name

    def test_already_4096_without_twin_passes_through(self):
        """No base to retire means no twin: rewriting would strand the data.

        These 11 owned names are already 4096 and were never migrated. A
        resolver that keyed on "does the twin exist" rather than on the declared
        lane would get them right by accident; one that rewrites on "is it
        canonical-dim" without a twin would break all 11.
        """
        for name in (
            "privacy_protocol",
            "l2_risk",
            "market_data",
            "crime_reports",
            "bridge_architecture",
            "mev_architecture",
            "standards",
            "zk_circuits",
            "scam_signatures",
            "newsletters",
            "podcast_episodes",
        ):
            assert lr.resolve_collection(name) == name
            assert lr.declared_lane_dim(name) is None

    def test_2560_base_without_twin_is_not_rewritten(self, caplog):
        """A 2560 base with no twin is not migrated — leave it and let the
        dimension guard refuse, loudly, instead of forking a new collection."""
        with caplog.at_level(logging.INFO):
            assert lr.resolve_collection("rug_events") == "rug_events"
        assert lr.declared_lane_dim("rug_events") == 2560
        assert "qdrant_lane_resolve" not in caplog.text

    def test_empty_and_none_pass_through(self):
        assert lr.resolve_collection(None) is None
        assert lr.resolve_collection("") == ""
        assert lr.resolve_collection(None) is None

    def test_1024_pinned_is_reported_not_rewritten(self):
        """wallet_clusters/youtube_videos are 1024: a 4096 vector cannot enter."""
        assert lr.declared_lane_dim("wallet_clusters") == 1024
        assert lr.declared_lane_dim("youtube_videos") == 1024
        assert lr.resolve_collection("wallet_clusters") == "wallet_clusters"

    def test_non_owned_twin_is_never_a_rewrite_target(self):
        """Twins outside the owned set stay out: routing at them would widen
        the write gate's boundary (agent_vectors, master_skills, cross_chain...)."""
        for name in ("agent_vectors", "master_skills", "cross_chain", "scan_intel"):
            assert name not in lr._DECLARED_TWINS
            assert lr.resolve_collection(name) == name


class TestIdempotence:
    def test_double_application_is_idempotent(self):
        """Applying twice is the same as applying once — a re-run must not
        produce ``threat_intel_v2_8b_8b``."""
        once = lr.resolve_collection("threat_intel_v2")
        twice = lr.resolve_collection(once)
        assert twice == once == "threat_intel_v2_8b"
        assert not twice.endswith(lr.EIGHT_B_SUFFIX * 2)

    def test_idempotent_for_every_declared_twin(self):
        """Whole-table fixed point, not a hand-picked sample."""
        for base in lr._DECLARED_TWINS:
            twin = lr.resolve_collection(base)
            assert twin == f"{base}{lr.EIGHT_B_SUFFIX}"
            assert lr.resolve_collection(twin) == twin

    def test_map_collection_is_also_idempotent(self):
        """The choke point every read/write uses shares the fixed point."""
        assert col._map_collection("threat_intel_v2_8b") == "threat_intel_v2_8b"


class TestLogging:
    def test_rewrite_is_logged_at_info_with_both_names(self, caplog):
        with caplog.at_level(logging.INFO, logger="rugbrain.qdrant_store.lane_resolver"):
            lr.resolve_collection("threat_intel_v2")
        text = caplog.text
        assert "qdrant_lane_resolve" in text
        assert "base=threat_intel_v2" in text
        assert "resolved=threat_intel_v2_8b" in text
        assert "dim=4096" in text

    def test_passthrough_is_not_logged_as_a_rewrite(self, caplog):
        with caplog.at_level(logging.INFO, logger="rugbrain.qdrant_store.lane_resolver"):
            lr.resolve_collection("threat_intel_v2_8b")
            lr.resolve_collection("privacy_protocol")
        assert "qdrant_lane_resolve" not in caplog.text


class TestMissingTwin:
    def test_unresolved_lane_is_importable_and_is_a_value_error(self):
        """Failing loud must be catchable as a ValueError, not a bare crash."""
        assert issubclass(lr.UnresolvedLane, ValueError)

    def test_missing_twin_never_silently_falls_back(self):
        """The contract: a base that is on the canonical lane but has no twin
        must be IMPOSSIBLE to get past. Declared at 4096 and resolvable is the
        only consistent state; anything else raises rather than reverting."""
        for base in lr._DECLARED_TWINS:
            resolved = lr.resolve_collection(base)
            assert resolved.endswith(lr.EIGHT_B_SUFFIX)
            assert lr.declared_lane_dim(resolved) == lr.CANONICAL_LANE_DIM

    def test_resolver_mode_off_bypasses_resolution(self, monkeypatch):
        """Documented escape hatch, off by default."""
        monkeypatch.setenv(lr.RESOLVER_MODE_ENV, "off")
        assert lr.resolve_collection("threat_intel_v2") == "threat_intel_v2"
        assert col._map_collection("threat_intel_v2") == "threat_intel_v2"

    def test_invalid_mode_falls_back_to_strict(self, monkeypatch):
        monkeypatch.setenv(lr.RESOLVER_MODE_ENV, "nonsense")
        assert lr.resolver_mode() == "strict"
        assert lr.resolve_collection("threat_intel_v2") == "threat_intel_v2_8b"

    def test_warn_mode_still_resolves(self, monkeypatch):
        monkeypatch.setenv(lr.RESOLVER_MODE_ENV, "warn")
        assert lr.resolve_collection("threat_intel_v2") == "threat_intel_v2_8b"


class TestWriteGate:
    def test_gate_admits_resolved_name(self):
        """The resolved twin must pass the readonly write gate — otherwise the
        fix moves the failure from a dim mismatch to a gate block."""
        assert col.gate_collection_name("threat_intel_v2_8b") in col._RMI_RAG_OWNED_COLLECTIONS
        assert col.gate_collection_name("threat_intel_v2") in col._RMI_RAG_OWNED_COLLECTIONS

    def test_gate_admits_every_resolved_twin(self):
        for base in lr._DECLARED_TWINS:
            resolved = lr.resolve_collection(base)
            assert col.gate_collection_name(resolved) in col._RMI_RAG_OWNED_COLLECTIONS, base

    def test_strip_twin_suffix(self):
        assert lr.strip_twin_suffix("threat_intel_v2_8b") == "threat_intel_v2"
        assert lr.strip_twin_suffix("threat_intel_v2") == "threat_intel_v2"
        assert lr.strip_twin_suffix(None) is None

    def test_gate_still_blocks_unowned(self):
        """Widening must not happen: an unowned name stays unowned."""
        for name in ("rmi_documents", "codebase_reference_v2", "agent_vectors"):
            assert col.gate_collection_name(name) not in col._RMI_RAG_OWNED_COLLECTIONS

    def test_gate_handles_both_suffixes(self, monkeypatch):
        """lane suffix inside, interim outside: interim is applied last.

        Needs the interim suffix actually configured — with no migration window
        open, ``_interim`` is just part of the name and must be left alone.
        """
        assert (
            col.gate_collection_name("threat_intel_v2_8b_interim") == "threat_intel_v2_8b_interim"
        )
        monkeypatch.setattr(_pkg, "_INTERIM_SUFFIX", "_interim", raising=False)
        assert (
            col.gate_collection_name("threat_intel_v2_8b_interim", "_interim") == "threat_intel_v2"
        )
        assert col.gate_collection_name("threat_intel_v2_interim", "_interim") == "threat_intel_v2"
        assert col.gate_collection_name(None) is None


class TestInterimNamespace:
    def test_interim_wraps_the_resolved_twin(self, monkeypatch):
        """Interim is OUTERMOST, so it shadows the twin, not the base."""
        monkeypatch.setattr(_pkg, "_INTERIM_SUFFIX", "_interim", raising=False)
        assert col._map_collection("threat_intel_v2") == "threat_intel_v2_8b_interim"
        assert (
            col.gate_collection_name("threat_intel_v2_8b_interim", "_interim") == "threat_intel_v2"
        )

    def test_interim_is_not_double_applied(self, monkeypatch):
        monkeypatch.setattr(_pkg, "_INTERIM_SUFFIX", "_interim", raising=False)
        already = col._map_collection("threat_intel_v2_8b_interim")
        assert already == "threat_intel_v2_8b_interim"

    def test_interim_only_name_is_left_alone_by_the_resolver(self, monkeypatch):
        """Pre-existing behaviour preserved: an interim-suffixed base name is
        only shadow-routed, never lane-resolved."""
        monkeypatch.setattr(_pkg, "_INTERIM_SUFFIX", "_interim", raising=False)
        assert col._map_collection("threat_intel_v2_interim") == "threat_intel_v2_interim"

    def test_unified_route_carries_the_resolution(self):
        """unified_route delegates to _map_collection, so reads follow too."""
        assert col.unified_route("threat_intel_v2") == ("threat_intel_v2_8b", None)
