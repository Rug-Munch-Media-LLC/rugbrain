#!/usr/bin/env python3
"""The shard contract: a partial collection must be able to BECOME complete.

Live defect this pins (2026-10-23): the chunk budget stops the elephant
mid-collection every night, so the only manifest entry that ever lands comes
from the incremental flush — which carried no ``expected_shards``. 0 of 5
sharded entries had one, and ``entry_is_done()`` asked only ``not partial``,
so ``rag_documents_8b`` (650k of 1,136,363 points) could never be finished no
matter how many nights it ran.

Run: python3 -m pytest tests/test_permanence_shard_contract.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from permanence_shard_contract import pin_expected_shards  # noqa: E402
from permanence_shard_resume import entry_is_done, resume_state  # noqa: E402


def test_pin_uses_ceiling_and_records_the_denominator() -> None:
    """1,136,363 points at 50k/shard is 23 shards, not 22 (never round down)."""
    entry = {"collection": "rag_documents_8b", "partial": True, "shards": []}
    pin_expected_shards(entry, 1_136_363, 50_000)
    assert entry["expected_shards"] == 23


def test_pin_never_lowers_a_denominator_and_survives_a_failed_count() -> None:
    """A count failure (0) keeps the proven value; growth raises it, never cuts."""
    entry = {"expected_shards": 23}
    pin_expected_shards(entry, 0, 50_000)
    assert entry["expected_shards"] == 23
    pin_expected_shards(entry, 1_500_000, 50_000)
    assert entry["expected_shards"] == 30


def test_partial_entry_with_all_shards_landed_is_done() -> None:
    """23 of 23 shards in B2 must finish the collection, flag or no flag."""
    entry = {
        "collection": "rag_documents_8b",
        "partial": True,
        "expected_shards": 23,
        "shards": [{"object_key": f"p{i}", "point_count": 50_000} for i in range(23)],
    }
    assert entry_is_done(entry)
    assert resume_state(entry)["shards"] == []


def test_partial_entry_short_of_its_denominator_is_not_done() -> None:
    """One shard short is still owed, and still resume-able."""
    entry = {
        "collection": "rag_documents_8b",
        "partial": True,
        "expected_shards": 23,
        "shards": [
            {"object_key": f"p{i}", "point_count": 50_000, "resume_cursor": i}
            for i in range(22)
        ],
    }
    assert not entry_is_done(entry)
    assert resume_state(entry)["points_done"] == 22 * 50_000


def test_entry_without_a_denominator_is_not_done() -> None:
    """The old shape (partial, no expected_shards) must NOT read as complete."""
    entry = {"collection": "rag_documents_8b", "partial": True, "shards": [{"object_key": "p0"}]}
    assert not entry_is_done(entry)
