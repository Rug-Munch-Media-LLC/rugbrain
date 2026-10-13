#!/usr/bin/env python3
"""Tests for the polymarket odds ingest.

These tests deliberately build their OWN parquet fixture in a temp dir. The
earlier version asserted a hardcoded 21 files, which only held on the machine
and the download session where it was written — the HF dataset is a 692MB
download that is not in the repo, and it has since been cleaned from disk
(verified: 0 parquet files present). A test whose expectation depends on an
external download is a test that fails for reasons unrelated to the code.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ingest_polymarket as ing  # noqa: E402


@pytest.fixture
def fake_dataset(tmp_path, monkeypatch):
    """Point the script at a temp dir holding exactly N parquet files."""
    data_dir = tmp_path / "polymarket"
    data_dir.mkdir()
    expected = 5
    for i in range(expected):
        (data_dir / f"part-{i}.parquet").write_bytes(b"PAR1")
    monkeypatch.setattr(ing, "DATA_DIR", str(data_dir))
    return expected


@pytest.mark.asyncio
async def test_dry_run_counts_every_file_present(fake_dataset):
    result = await ing.main(dry_run=True, b2_upload=False, limit_files=None)
    assert result["files_processed"] == fake_dataset
    assert result["errors"] == 0
    assert result.get("uploaded", 0) == 0, "a dry run must not upload"


@pytest.mark.asyncio
async def test_missing_data_dir_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(ing, "DATA_DIR", str(tmp_path / "nope"))
    result = await ing.main(dry_run=True, b2_upload=False, limit_files=None)
    assert result["errors"] == 1
    assert result["files_processed"] == 0


@pytest.mark.asyncio
async def test_limit_is_respected(fake_dataset, monkeypatch):
    result = await ing.main(dry_run=True, b2_upload=False, limit_files=2)
    assert result["files_processed"] == 2
