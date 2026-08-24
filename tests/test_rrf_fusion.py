"""RRF fusion core contracts (the original reciprocal-rank module).

* rank-position scoring: 1/(k+rank), weighted per pillar
* multi-list accumulation: docs in multiple lists sum their contributions
* entity boost multiplies matching docs' contributions
* dedup: first-seen payload wins; output sorted by fused score desc
* junk rows (no doc_id/id) skipped
"""

from __future__ import annotations

import pytest

from rugbrain.rrf_fusion import _rrf_fuse


def _doc(doc_id, text="body"):
    return {"doc_id": doc_id, "content": text}


def test_single_list_rank_positions():
    out = _rrf_fuse([[_doc("a"), _doc("b"), _doc("c")]], k=60)
    assert [r["doc_id"] for r in out] == ["a", "b", "c"]
    # scores rounded to 6 decimals upstream
    assert out[0]["rrf_score"] == pytest.approx(1 / 61, abs=1e-5)
    assert out[1]["rrf_score"] == pytest.approx(1 / 62, abs=1e-5)


def test_multi_list_accumulates():
    out = _rrf_fuse(
        [
            [_doc("shared"), _doc("only-a")],
            [_doc("shared")],
        ],
        k=60,
    )
    by_id = {r["doc_id"]: r for r in out}
    # shared appears rank-1 in both lists: 2 * (1/61) > only-a's 1/62
    assert out[0]["doc_id"] == "shared"
    assert by_id["shared"]["rrf_score"] == pytest.approx(2 / 61, abs=1e-5)


def test_pillar_weights_scale_contributions():
    out = _rrf_fuse(
        [
            [_doc("a")],
            [_doc("b")],
        ],
        k=60,
        pillar_weights=[2.0, 1.0],
    )
    by_id = {r["doc_id"]: r for r in out}
    assert by_id["a"]["rrf_score"] == pytest.approx(2 / 61, abs=1e-5)
    assert by_id["b"]["rrf_score"] == pytest.approx(1 / 61, abs=1e-5)
    assert out[0]["doc_id"] == "a"


def test_entity_boost_applies_and_flagged():
    out = _rrf_fuse(
        [
            [_doc("entity-doc"), _doc("plain")],
        ],
        k=60,
        entity_doc_ids={"entity-doc"},
    )
    by_id = {r["doc_id"]: r for r in out}
    assert by_id["entity-doc"]["entity_match"] is True
    assert by_id["entity-doc"]["rrf_score"] == pytest.approx((1 / 61) * 1.5, abs=1e-5)
    assert by_id["plain"]["entity_match"] is False


def test_junk_rows_skipped():
    out = _rrf_fuse([[{"no_id": True}, _doc("real"), {"id": ""}]])
    assert [r["doc_id"] for r in out] == ["real"]


def test_empty_lists_safe():
    assert _rrf_fuse([]) == []
    assert _rrf_fuse([[]]) == []


def test_first_seen_payload_wins():
    out = _rrf_fuse(
        [
            [_doc("d", "first-body")],
            [{"doc_id": "d", "content": "second-body"}],
        ]
    )
    assert out[0]["content"] == "first-body"
