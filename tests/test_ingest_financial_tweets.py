#!/usr/bin/env python3
"""Tests for the labelled financial-tweets sentiment ingest.

The script loads through ``datasets.load_dataset`` (the HF hub), NOT through
``open`` -- an earlier version of this test mocked ``builtins.open``, which
never fires, and the script then bailed out with errors=1 because ``datasets``
is not installed in the venv. These tests inject a stub ``datasets`` module and
patch ``load_dataset`` at the seam the script actually calls.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ingest_financial_tweets as ing

ROWS = [
    {"tweet": "$BTC is going to the moon!", "sentiment": "Bullish", "url": "https://example.com/1"},
    {"tweet": "$ETH downtrend continues", "sentiment": "Bearish", "url": "https://example.com/2"},
    {"tweet": "", "sentiment": "Neutral", "url": ""},  # blank -> skipped
    {"tweet": " $XRP rally", "sentiment": "Bullish", "url": ""},
]


class _FakeDataset:
    """Minimal stand-in for a HF Dataset split."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def __len__(self) -> int:
        return len(self._rows)

    def __getitem__(self, idx):
        return self._rows[idx]

    def __iter__(self):
        return iter(self._rows)


@pytest.fixture
def fake_datasets(monkeypatch):
    """Install a stub ``datasets`` module so the lazy import resolves."""
    module = types.ModuleType("datasets")
    module.load_dataset = MagicMock(return_value=_FakeDataset(list(ROWS)))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "datasets", module)
    return module


def _mocks() -> tuple:
    embedder = AsyncMock()
    embedder.embed.return_value = [[0.1] * 4096 for _ in ROWS]
    store_cls = MagicMock()
    store = AsyncMock()
    store.insert = AsyncMock(return_value=True)
    store_cls.return_value = store
    return embedder, store_cls, store


@pytest.mark.asyncio
async def test_ingests_and_skips_blank_tweets(fake_datasets):
    embedder, store_cls, store = _mocks()
    with (
        patch.object(ing, "get_embedder", return_value=embedder, create=True),
        patch.object(ing, "QdrantVectorStore", store_cls, create=True),
    ):
        stats = await ing.main(limit=None, dry_run=False, batch_size=10)

    assert stats["fetched"] == len(ROWS)
    assert stats["embedded"] == 3, "the blank tweet must be skipped"
    assert stats["errors"] == 0
    assert store.insert.await_count == 3
    fake_datasets.load_dataset.assert_called_once()


@pytest.mark.asyncio
async def test_dry_run_embeds_nothing(fake_datasets):
    embedder, store_cls, store = _mocks()
    with (
        patch.object(ing, "get_embedder", return_value=embedder, create=True),
        patch.object(ing, "QdrantVectorStore", store_cls, create=True),
    ):
        stats = await ing.main(limit=2, dry_run=True, batch_size=10)

    assert stats["fetched"] == len(ROWS)
    assert stats["embedded"] == 0
    embedder.embed.assert_not_awaited()
    store.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_loader_failure_is_reported_not_raised(monkeypatch):
    """A hub failure must surface as errors=1, never an exception."""
    module = types.ModuleType("datasets")
    module.load_dataset = MagicMock(side_effect=RuntimeError("hub down"))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "datasets", module)
    stats = await ing.main(limit=1, dry_run=True, batch_size=1)
    assert stats["errors"] == 1
    assert stats["fetched"] == 0
