"""Tests for scripts.ingest_continuous — checkpoint scheduler + jobs."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow importing the script as a module (repo scripts rely on the
# installed package; this makes the scheduler testable in-process).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ingest_continuous as mod


def _no_redis(monkeypatch) -> None:
    """Force the Redis client to fail fast so state falls back to memory."""
    monkeypatch.setattr(mod, "REDIS_HOST", "127.0.0.1")
    monkeypatch.setattr(mod, "REDIS_PORT", 1)


def test_jobs_cadence():
    names = {j.name: j.interval for j in mod.JOBS}
    assert names["security_alerts"] == 5 * 60
    assert names["market_snapshot"] == 5 * 60
    assert names["news_crawl"] == 15 * 60
    assert names["social_intel"] == 15 * 60
    assert names["codebase_repos"] == 60 * 60
    assert names["full_recrawl"] == 6 * 60 * 60


def test_get_job():
    assert mod.get_job("news_crawl") is not None
    assert mod.get_job("does_not_exist") is None


async def test_checkpoint_store_memory_fallback(monkeypatch):
    _no_redis(monkeypatch)
    store = mod.CheckpointStore()
    assert await store.last_run("job1") is None
    assert await store.due("job1", 60) is True
    await store.mark("job1", 1000.0)
    assert await store.last_run("job1") == 1000.0
    assert await store.due("job1", 60, now=1030.0) is False
    assert await store.due("job1", 60, now=1060.0) is True
    await store.reset()
    assert await store.last_run("job1") is None


async def test_run_job_due_marks_checkpoint(monkeypatch):
    _no_redis(monkeypatch)
    store = mod.CheckpointStore()
    calls: list[bool] = []

    async def fake_run(dry_run: bool) -> dict:
        calls.append(dry_run)
        return {"ok": True}

    job = mod.Job("fake", 60, fake_run)
    result = await mod.run_job(job, store, dry_run=True)
    assert result["ok"] is True
    assert result["job"] == "fake"
    assert calls == [True]
    assert await store.last_run("fake") is not None
    # Not due now → skipped, no second call.
    result2 = await mod.run_job(job, store)
    assert result2.get("skipped") is True
    assert len(calls) == 1


async def test_run_job_failure_does_not_checkpoint(monkeypatch):
    _no_redis(monkeypatch)
    store = mod.CheckpointStore()

    async def boom(dry_run: bool) -> dict:
        raise RuntimeError("boom")

    job = mod.Job("boom", 60, boom)
    result = await mod.run_job(job, store)
    assert result["ok"] is False
    assert "error" in result
    assert await store.last_run("boom") is None  # retried next tick


async def test_run_job_force_runs_regardless(monkeypatch):
    _no_redis(monkeypatch)
    store = mod.CheckpointStore()
    await store.mark("forced", 0.0)
    calls = 0

    async def fake_run(dry_run: bool) -> dict:
        nonlocal calls
        calls += 1
        return {"ok": True}

    job = mod.Job("forced", 3600, fake_run)
    result = await mod.run_job(job, store, force=True)
    assert result["ok"] is True
    assert calls == 1


async def test_run_due_filters_by_names(monkeypatch):
    _no_redis(monkeypatch)
    store = mod.CheckpointStore()
    results = await mod.run_due(store, names=("security_alerts", "market_snapshot"))
    assert len(results) == 2
    assert {r["job"] for r in results} == {"security_alerts", "market_snapshot"}


async def test_job_market_snapshot_wiring(monkeypatch):
    from rmi_rag import market_snapshot as ms

    async def fake_build(client=None):
        return {
            "generated_at": "2026-08-04T00:00:00Z",
            "fear_greed": {"value": 50, "classification": "neutral"},
        }

    monkeypatch.setattr(ms, "build_market_snapshot", fake_build)
    monkeypatch.setattr(ms.MarketSnapshotStore, "store", lambda self, snapshot: True)

    result = await mod._job_market_snapshot(dry_run=False)
    assert result["source"] == "market_snapshot"
    assert result["stored"] is True

    dry = await mod._job_market_snapshot(dry_run=True)
    assert dry["stored"] is False


async def test_checkpoint_store_redis_unreachable_returns_none(monkeypatch):
    _no_redis(monkeypatch)
    store = mod.CheckpointStore()
    client = await store._client()
    assert client is None
