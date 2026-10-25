"""The rb-vec26 guard: a foreign vector width must never be written."""

import pytest

from rugbrain.vector_schema_guard import SchemaMismatch, declared_schemas, guard_collection

BARE_4096 = {"result": {"config": {"params": {"vectors": {"size": 4096, "distance": "Cosine"}}}}}
BARE_2560 = {"result": {"config": {"params": {"vectors": {"size": 2560, "distance": "Cosine"}}}}}
NAMED = {"result": {"config": {"params": {"vectors": {"dense": {"size": 4096, "distance": "Cosine"}}}}}}


def test_bare_slot_parsed():
    assert [(s.name, s.size) for s in declared_schemas(BARE_4096)] == [("", 4096)]


def test_matching_dim_accepted():
    assert guard_collection(BARE_4096, lane_dim=4096).size == 4096


def test_2560_into_4096_refused():
    with pytest.raises(SchemaMismatch, match="4096-d"):
        guard_collection(BARE_4096, lane_dim=2560)


def test_4096_into_2560_refused():
    with pytest.raises(SchemaMismatch):
        guard_collection(BARE_2560, lane_dim=4096)


def test_named_slot_shaped():
    schema = guard_collection(NAMED, lane_dim=4096, vector_name="dense")
    assert schema.shape([0.0]) == {"dense": [0.0]}


def test_distance_mismatch_refused():
    with pytest.raises(SchemaMismatch, match="distance"):
        guard_collection(BARE_4096, lane_dim=4096, expected_distance="Dot")
