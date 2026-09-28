"""Regression tests for content draft generation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from rugbrain import content_candidates, content_engine


@pytest.mark.asyncio
async def test_generate_channel_drafts_handles_no_stories(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty story pool must return drafts instead of calling asyncio.wait([])."""
    monkeypatch.setattr(content_candidates, "recent_stories", AsyncMock(return_value=[]))
    monkeypatch.setattr(content_engine, "select_scans", AsyncMock(return_value=[]))
    content_engine._drafts_cache["data"] = None
    content_engine._drafts_cache["ts"] = 0.0

    result = await content_engine.generate_channel_drafts("diagnostic-empty")

    assert result == {"main": [], "alerts": [], "news": [], "scans": []}
