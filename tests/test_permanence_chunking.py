#!/usr/bin/env python3
"""Unit tests for the chunked permanence path.

These cover the three behaviours that failed live on 2026-10-18..23:
a chunk plan that never concentrates the elephants, a resume that is
idempotent, and a manifest that admits it is partial.

Run: python3 -m pytest tests/test_permanence_chunking.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from permanence_chunk_plan import plan_chunks  # noqa: E402
from permanence_chunk_run import run_chunk  # noqa: E402


def test_plan_keeps_every_multi_item_chunk_within_budget() -> None:
    """Sizing is by cost, not by name count: a 27.7min + 21.9min pair is fine
    inside an 80min chunk; what must never happen is a chunk over budget that
    holds more than one item (that is the case chunking exists to prevent)."""
    names = ["rag_documents_8b", "news_v2_8b", "cbrv2_8b_hnswdisk_pilot", "small_a"]
    plan = plan_chunks(names, budget_minutes=80)
    for chunk in plan:
        if len(chunk.collections) > 1:
            assert chunk.est_minutes <= 80, f"over-budget chunk: {chunk}"
    assert max(c.est_minutes for c in plan) <= 80


def test_plan_is_deterministic_and_covers_every_collection() -> None:
    names = [f"c{i:02d}" for i in range(40)] + ["rag_documents_8b"]
    first = plan_chunks(names, budget_minutes=10)
    second = plan_chunks(names, budget_minutes=10)
    assert [c.collections for c in first] == [c.collections for c in second]
    flat = [n for chunk in first for n in chunk.collections]
    assert sorted(flat) == sorted(names)
    assert len(flat) == len(set(flat)), "a collection was planned twice"


def test_plan_respects_the_budget_except_for_an_unsplittable_item() -> None:
    plan = plan_chunks(["rag_documents_8b", "a", "b", "c"], budget_minutes=30)
    for chunk in plan:
        if len(chunk.collections) > 1:
            assert chunk.est_minutes <= 30


def test_run_chunk_defers_when_the_budget_is_spent() -> None:
    calls: list[str] = []

    def snap(name: str) -> dict:
        calls.append(name)
        return {"collection": name, "point_count": 1}

    result = run_chunk(
        ["a", "b", "c", "d"],
        snapshot_fn=snap,
        flush_fn=lambda entries, failed: None,
        budget_seconds=0.0,
    )
    assert result.done == []
    assert result.deferred == ["a", "b", "c", "d"]
    assert not result.ok


def test_run_chunk_flushes_after_every_collection_so_a_kill_loses_nothing() -> None:
    flushes: list[int] = []

    def flush(entries: list[dict], failed: dict) -> None:
        flushes.append(len(entries))

    result = run_chunk(
        ["a", "b", "c"],
        snapshot_fn=lambda n: {"collection": n, "point_count": 2},
        flush_fn=flush,
        budget_seconds=3600,
    )
    assert result.done == ["a", "b", "c"]
    assert flushes == [1, 2, 3], "manifest must be flushed incrementally"
    assert result.points == 6


def test_run_chunk_records_a_failure_and_continues() -> None:
    seen: list[dict] = []

    def snap(name: str) -> dict:
        if name == "broken":
            raise RuntimeError("503 service_unavailable no tomes available")
        return {"collection": name, "point_count": 5}

    result = run_chunk(
        ["ok1", "broken", "ok2"],
        snapshot_fn=snap,
        flush_fn=lambda entries, failed: seen.append(dict(failed)),
        budget_seconds=3600,
    )
    assert result.done == ["ok1", "ok2"]
    assert "broken" in result.failed
    assert "503" in result.failed["broken"]
    assert seen[-1], "the failure must reach the manifest flush"
    assert not result.ok
