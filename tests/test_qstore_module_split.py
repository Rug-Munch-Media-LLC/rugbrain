"""The pieces extracted from the collections/write_lane split.

Each module here was split out to own ONE job, and each job has an invariant
that is easy to break by moving code between modules:

* ``collection_schema`` — a reader and a writer must never disagree about a
  schema, so both the legacy bare-vector and the named-vector shapes must parse,
  and a malformed config must yield an empty set rather than an exception (it
  is called on a partially-populated probe response).
* ``collections`` — the compat facade must re-export the SAME objects its owning
  modules define, not copies. A copy is how a monkeypatch on the package
  silently stops applying.
* ``lane_widths`` — the widths are measured constants, so the one thing to pin
  is that they do not follow ``RAG_EMBED_DIM``.
"""

from __future__ import annotations

import rugbrain.qdrant_store as _pkg
from rugbrain.qdrant_store import collection_lifecycle, namespace, owned_registry, ownership
from rugbrain.qdrant_store import collections as facade
from rugbrain.qdrant_store.collection_schema import dense_vector_dimensions
from rugbrain.qdrant_store.lane_widths import (
    CANONICAL_BASES,
    TWIN_LANE_DIM,
    declared_width,
)


class TestDenseVectorDimensions:
    def test_legacy_bare_vector(self):
        """Pre-hybrid collections carry ``{"size": N}`` with no vector name."""
        assert dense_vector_dimensions({"vectors": {"size": 4096}}) == {4096}

    def test_named_vectors(self):
        """Post-migration hybrid collections carry one entry per vector name."""
        params = {"vectors": {"dense": {"size": 4096}, "bm25": {}}}
        assert dense_vector_dimensions(params) == {4096}

    def test_two_dense_named_vectors_are_both_reported(self):
        """A reader must see BOTH widths, or it will call one of them a mismatch."""
        params = {"vectors": {"dense": {"size": 4096}, "sparse_dense": {"size": 2560}}}
        assert dense_vector_dimensions(params) == {4096, 2560}

    def test_missing_or_malformed_config_yields_empty_not_an_error(self):
        """Called on a probe response that may be half-populated; must not raise.

        An empty set means "no constraint declared", which the lifecycle check
        reads as permissive. Raising here would turn a malformed probe into an
        insert failure.
        """
        assert dense_vector_dimensions({}) == set()
        assert dense_vector_dimensions({"vectors": None}) == set()
        assert dense_vector_dimensions({"vectors": "nonsense"}) == set()
        assert dense_vector_dimensions({"vectors": {"dense": {}}}) == set()
        assert dense_vector_dimensions({"vectors": {"dense": {"size": "4096"}}}) == set()


class TestDeclaredWidth:
    def test_twin_is_the_measured_4096(self):
        assert declared_width("threat_intel_v2_8b") == TWIN_LANE_DIM

    def test_base_of_a_declared_twin_is_the_retired_generation(self):
        assert declared_width("threat_intel_v2") != TWIN_LANE_DIM

    def test_already_canonical_base_is_4096(self):
        """No twin and no rewrite, so the base itself is the 4096 lane."""
        assert declared_width("privacy_protocol") == TWIN_LANE_DIM
        assert "privacy_protocol" in CANONICAL_BASES

    def test_pinned_names_report_their_own_width(self):
        assert declared_width("wallet_clusters") == 1024
        assert declared_width("rug_events") == 2560

    def test_undeclared_name_is_none(self):
        """None is what makes the write path DEFER rather than raise."""
        assert declared_width("scratch_coll") is None
        assert declared_width("scratch_coll_8b") is None

    def test_widths_do_not_follow_the_embedder_env(self, monkeypatch):
        """THE regression on the root cause: a declaration that moves with the
        same env var as the vector it is checked against is not a declaration."""
        monkeypatch.setenv("RAG_EMBED_DIM", "2560")
        assert declared_width("threat_intel_v2_8b") == 4096


class TestCompatFacade:
    """The facade must re-export the same objects, never copies."""

    def test_reexports_are_identical_objects(self):
        assert facade._RMI_RAG_OWNED_COLLECTIONS is owned_registry._RMI_RAG_OWNED_COLLECTIONS
        assert facade._RMI_RAG_QDRANT_WRITE_MODE == owned_registry._RMI_RAG_QDRANT_WRITE_MODE
        assert facade._INTERIM_SUFFIX == namespace._INTERIM_SUFFIX
        assert facade.gate_collection_name is ownership.gate_collection_name
        assert facade._map_collection is namespace.map_collection
        assert facade._ensure_collection is collection_lifecycle._ensure_collection

    def test_package_namespace_exposes_the_same_objects(self):
        """store_* resolve shared names through the PACKAGE, so the two must
        agree — otherwise a test patch on one has no effect on the other."""
        assert _pkg._map_collection is namespace.map_collection
        assert _pkg._RMI_RAG_OWNED_COLLECTIONS is owned_registry._RMI_RAG_OWNED_COLLECTIONS
        assert _pkg.gate_collection_name is ownership.gate_collection_name
        assert _pkg._ensure_collection is collection_lifecycle._ensure_collection

    def test_no_logic_accumulated_in_the_facade(self):
        """Guard against the facade growing back into a second implementation."""
        source = (__import__("pathlib").Path(facade.__file__)).read_text()
        assert source.count("def ") == 0, "the facade re-exports only; add no functions"
