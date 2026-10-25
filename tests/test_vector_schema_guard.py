"""Schema-guard tests: declared vector config vs the lane's model dimension.

Regression class: 2026-10-25 -- a 4096-d writer posted an UNNAMED vector into
``news_v2_8b`` (named slot ``dense``) and Qdrant answered HTTP 400
``Wrong input: Not existing vector name error:``. 299,069 usable points looked
unreachable. These tests pin both halves: dimension AND slot shape.
"""

from __future__ import annotations

import json
import os
import urllib.request

import pytest

from rugbrain.vector_schema_guard import (
    UNNAMED,
    SchemaMismatch,
    declared_schemas,
    guard_collection,
)

NAMED_4096 = {
    "result": {"config": {"params": {"vectors": {"dense": {"size": 4096, "distance": "Cosine"}}}}}
}
BARE_4096 = {"result": {"config": {"params": {"vectors": {"size": 4096, "distance": "Cosine"}}}}}
BARE_2560 = {"result": {"config": {"params": {"vectors": {"size": 2560, "distance": "Cosine"}}}}}


def test_named_slot_is_parsed_with_its_name() -> None:
    assert declared_schemas(NAMED_4096)[0].name == "dense"


def test_bare_slot_is_unnamed() -> None:
    assert declared_schemas(BARE_4096)[0].name == UNNAMED


def test_named_slot_accepts_matching_lane_and_returns_named_shape() -> None:
    slot = guard_collection(NAMED_4096, lane_dim=4096, lane_model="Qwen/Qwen3-Embedding-8B")
    assert slot.name == "dense"
    assert slot.shape([0.1] * 4096) == {"dense": [0.1] * 4096}


def test_dimension_mismatch_fails_closed() -> None:
    with pytest.raises(SchemaMismatch, match="2560-d but lane model"):
        guard_collection(BARE_2560, lane_dim=4096, lane_model="Qwen/Qwen3-Embedding-8B")


def test_wrong_slot_name_fails_closed() -> None:
    with pytest.raises(SchemaMismatch, match="not in collection"):
        guard_collection(NAMED_4096, lane_dim=4096, vector_name="text")


def test_multi_slot_collection_requires_a_name() -> None:
    multi = {"result": {"config": {"params": {"vectors": {
        "dense": {"size": 4096, "distance": "Cosine"},
        "sparse": {"size": 1024, "distance": "Dot"},
    }}}}}
    with pytest.raises(SchemaMismatch, match="multiple vector slots"):
        guard_collection(multi, lane_dim=4096)


def test_distance_mismatch_fails_closed() -> None:
    dot = {"result": {"config": {"params": {"vectors": {"size": 4096, "distance": "Dot"}}}}}
    with pytest.raises(SchemaMismatch, match="distance Dot"):
        guard_collection(dot, lane_dim=4096)


@pytest.mark.integration
def test_live_corpus_collections_match_the_8b_lane() -> None:
    """Live check: every corpus collection honours the 4096-d 8B contract."""
    url = os.environ.get("QDRANT_URL", "http://127.0.0.1:6334")
    for name in ("codebase_reference_v2", "news_v2_8b", "rag_documents", "rag_documents_8b"):
        try:
            with urllib.request.urlopen(f"{url}/collections/{name}", timeout=5) as resp:
                info = json.loads(resp.read())
        except OSError as exc:  # qdrant unreachable here -> not this lane's failure
            pytest.skip(f"{name}: qdrant unreachable ({exc})")
        slot = guard_collection(info, lane_dim=4096, lane_model="Qwen/Qwen3-Embedding-8B")
        assert slot.size == 4096