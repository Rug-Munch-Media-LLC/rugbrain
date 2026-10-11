"""Golden file schema validation for eval_ingest_sources."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rugbrain.eval.golden_set import GoldenQuery, load_golden

GOLDEN_PATH = Path("eval/golden/ingest_sources_golden.jsonl")


def test_golden_set_loads():
    """Load the golden set and verify every row validates against GoldenQuery schema."""
    queries = load_golden(GOLDEN_PATH)
    assert len(queries) > 0, "Golden set is empty"

    for i, q in enumerate(queries):
        assert isinstance(q.query, str), f"Row {i}: query must be str"
        assert isinstance(q.gold_chunks, list), f"Row {i}: gold_chunks must be list"
        assert isinstance(q.collection, str), f"Row {i}: collection must be str"
        assert len(q.gold_chunks) > 0, f"Row {i}: at least one gold_chunk required"
        assert q.collection in {
            "scam_intel_v2_8b",
            "known_scams_8b",
            "threat_intel_v2_8b",
            "news_v2_8b",
        }, f"Row {i}: unexpected collection {q.collection}"
        assert q.has_gold, f"Row {i}: must have gold_chunks or relevant_doc_ids"


def test_golden_set_queries_spread_across_collections():
    """Ensure golden queries span all 4 new ingest collections."""
    queries = load_golden(GOLDEN_PATH)
    collections = {q.collection for q in queries}
    required = {"scam_intel_v2_8b", "known_scams_8b", "threat_intel_v2_8b", "news_v2_8b"}
    assert collections == required, f"Missing collections: {required - collections}"


def test_golden_query_count_in_range():
    """Query count within 25-40 range per spec."""
    queries = load_golden(GOLDEN_PATH)
    assert 25 <= len(queries) <= 40, f"Golden set has {len(queries)} queries, expected 25-40"
