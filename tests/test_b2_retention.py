"""Tests for b2_permanence snapshot retention/prune logic.

Covers the pure, network-free parts of the retention policy:
  - parse_snapshot_date (what object keys are prunable)
  - retention_keep_set (30 daily / 4 weekly / 12 monthly)
  - prune_snapshots safety guards (permanent bucket, floor guard)
  - dry-run default behavior (no deletion without --prune-live)

These tests are self-contained: they use a fake bucket and never touch
B2, Qdrant, or the network. Importing b2_permanence requires qdrant_client
and b2sdk — both are dev dependencies of the repo.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import b2_permanence  # noqa: E402

# ── Fixtures / fakes ───────────────────────────────────────────────────


def make_dates(start: str, end: str) -> list[str]:
    """Return daily YYYY-MM-DD strings from start (inclusive) to end (inclusive)."""
    dates: list[str] = []
    d = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    while d <= end_dt:
        dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return dates


class _FakeVersion:
    def __init__(self, file_name: str):
        self.file_name = file_name
        self.id_ = f"file_id_{file_name}"


class FakeBucket:
    """In-memory bucket exposing just the b2sdk surface prune needs."""

    def __init__(self, keys: list[str]):
        self._keys = keys
        self.deleted: list[str] = []

    def ls(self, prefix: str, recursive: bool = True) -> Any:
        return [(_FakeVersion(k), None) for k in self._keys if k.startswith(prefix)]

    def delete_file_version(self, file_id: str, file_name: str) -> None:
        self.deleted.append(file_name)


class FakeApi:
    def __init__(self, keys: list[str]):
        self.bucket = FakeBucket(keys)
        self._deleted: list[str] = []

    def ensure_bucket(self, name: str) -> Any:
        return self.bucket


# ── parse_snapshot_date ────────────────────────────────────────────────


def test_parse_snapshot_date_accepts_dated_dir_objects() -> None:
    assert b2_permanence.parse_snapshot_date("snapshots/2026-08-15/news.parquet") == "2026-08-15"
    assert b2_permanence.parse_snapshot_date("snapshots/2026-08-15/manifest.json") == "2026-08-15"


def test_parse_snapshot_date_rejects_pointers_and_strays() -> None:
    assert b2_permanence.parse_snapshot_date("snapshots/latest.json") is None
    assert b2_permanence.parse_snapshot_date("snapshots/manifest.json") is None
    assert b2_permanence.parse_snapshot_date("latest.json") is None
    assert b2_permanence.parse_snapshot_date("other/2026-08-15/x") is None
    assert b2_permanence.parse_snapshot_date("2026-08-15/foo.parquet") is None


def test_parse_snapshot_date_rejects_malformed_dates() -> None:
    assert b2_permanence.parse_snapshot_date("snapshots/2026-13-99/x") is None
    assert b2_permanence.parse_snapshot_date("snapshots/2026-02-30/x") is None
    assert b2_permanence.parse_snapshot_date("snapshots/20260815/x") is None
    assert b2_permanence.parse_snapshot_date("snapshots/not-a-date/x") is None


# ── retention_keep_set ─────────────────────────────────────────────────


def test_retention_keeps_newest_30_daily() -> None:
    dates = make_dates("2026-01-01", "2026-03-01")  # 60 days
    keep = b2_permanence.retention_keep_set(dates)
    assert len(keep) >= 30
    assert max(dates) in keep
    # Oldest dates (beyond daily+weekly+monthly windows) are pruned
    assert dates[0] not in keep


def test_retention_monthly_tier_extends_beyond_daily_window() -> None:
    # 200 days spans ~7 calendar months; monthly keeps the newest per month,
    # so keep count grows beyond the 30-day daily window.
    dates = make_dates("2025-08-15", "2026-03-01")
    keep = b2_permanence.retention_keep_set(dates)
    assert len(keep) > 30
    assert max(dates) in keep
    # A mid-period monthly snapshot (e.g. earliest kept) is far older than 30d
    assert dates[0] not in keep


def test_retention_small_history_kept_entirely() -> None:
    dates = make_dates("2026-08-01", "2026-08-15")  # 15 days < 30
    keep = b2_permanence.retention_keep_set(dates)
    assert keep == set(dates)


def test_retention_edge_cases() -> None:
    assert b2_permanence.retention_keep_set([]) == set()
    assert b2_permanence.retention_keep_set(["2026-08-15"]) == {"2026-08-15"}


def test_retention_never_drops_newest_date() -> None:
    for n in (1, 5, 40, 200):
        dates = make_dates(
            "2026-01-01", (datetime(2026, 1, 1) + timedelta(days=n - 1)).strftime("%Y-%m-%d")
        )
        keep = b2_permanence.retention_keep_set(dates)
        assert max(dates) in keep


# ── prune_snapshots safety guards ──────────────────────────────────────


def test_prune_refuses_permanent_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(["snapshots/2026-08-15/news.parquet"])
    monkeypatch.setattr(b2_permanence, "ensure_bucket", lambda api_, name: api.ensure_bucket(name))
    result = b2_permanence.prune_snapshots(api, "rugmunch-permanent", dry_run=True)
    assert result.get("refused") == "permanent-bucket"
    assert api.bucket.deleted == []


def test_prune_refuses_empty_keep_set(monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeApi(["snapshots/2026-08-15/news.parquet"])
    monkeypatch.setattr(b2_permanence, "ensure_bucket", lambda api_, name: api.ensure_bucket(name))
    monkeypatch.setattr(b2_permanence, "retention_keep_set", lambda *a, **k: set())
    result = b2_permanence.prune_snapshots(api, "rmi-rag-permanence", dry_run=True)
    assert result.get("refused") == "floor-guard"
    assert api.bucket.deleted == []


def test_prune_dry_run_lists_without_deleting(monkeypatch: pytest.MonkeyPatch) -> None:
    # 60 daily snapshots with a tight policy → old dirs flagged, nothing deleted
    dates = make_dates("2026-01-01", "2026-03-01")
    keys = [f"snapshots/{d}/news.parquet" for d in dates]
    api = FakeApi(keys)
    monkeypatch.setattr(b2_permanence, "ensure_bucket", lambda api_, name: api.ensure_bucket(name))

    result = b2_permanence.prune_snapshots(
        api,
        "rmi-rag-permanence",
        dry_run=True,
        keep_daily=10,
        keep_weekly=2,
        keep_monthly=3,
    )
    assert result["snapshot_dates"] == len(dates)
    assert result["would_delete"] > 0
    assert "refused" not in result
    # Dry-run must not delete anything
    assert api.bucket.deleted == []
    # Newest snapshot always survives
    assert max(dates) in b2_permanence.retention_keep_set(dates, 10, 2, 3)


def test_prune_live_deletes_only_unprotected_dirs(monkeypatch: pytest.MonkeyPatch) -> None:
    # 60 daily snapshots; live prune with a tight policy must delete the old
    # dirs but never the newest, and never latest.json at the prefix root.
    dates = make_dates("2026-01-01", "2026-03-01")
    keys = [f"snapshots/{d}/news.parquet" for d in dates] + ["snapshots/latest.json"]
    api = FakeApi(keys)
    monkeypatch.setattr(b2_permanence, "ensure_bucket", lambda api_, name: api.ensure_bucket(name))

    result = b2_permanence.prune_snapshots(
        api,
        "rmi-rag-permanence",
        dry_run=False,
        keep_daily=10,
        keep_weekly=2,
        keep_monthly=3,
    )
    assert result["deleted_objects"] > 0
    deleted_dirs = result["deleted_dirs"]
    # Newest date never deleted
    assert max(dates) not in deleted_dirs
    # Pointer at prefix root never deleted
    assert "snapshots/latest.json" not in api.bucket.deleted
    # Only dated-dir objects were deleted
    for fname in api.bucket.deleted:
        assert fname.startswith("snapshots/")
        assert b2_permanence.parse_snapshot_date(fname) is not None
