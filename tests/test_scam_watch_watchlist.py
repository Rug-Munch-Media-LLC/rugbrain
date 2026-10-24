"""The watchlist is closed-schema, deduped and public-data only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from rugbrain.scam_watch.models import ChannelRecord
from rugbrain.scam_watch.watchlist import DEFAULT_WATCHLIST_PATH, load_watchlist


def test_seed_watchlist_loads_with_unique_handles():
    records = load_watchlist()
    assert len(records) >= 10
    assert len({record.handle.lower() for record in records}) == len(records)
    assert all(record.channel_url.startswith("https://www.youtube.com/") for record in records)


def test_seed_has_no_unsourced_subscriber_counts():
    assert all(record.subs is None for record in load_watchlist())
    assert all(record.risk_history == () for record in load_watchlist())


def test_duplicate_handle_file_is_rejected(tmp_path: Path):
    payload = json.loads(DEFAULT_WATCHLIST_PATH.read_text(encoding="utf-8"))
    payload["channels"].append(payload["channels"][0])
    target = tmp_path / "watchlist.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate handles"):
        load_watchlist(target)


def test_wrong_version_is_rejected(tmp_path: Path):
    target = tmp_path / "watchlist.json"
    target.write_text(json.dumps({"version": 99, "channels": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        load_watchlist(target)


@pytest.mark.parametrize("channel_url", ["https://example.com/@x", "http://www.youtube.com/@x"])
def test_non_youtube_urls_are_rejected(channel_url: str):
    with pytest.raises(ValidationError):
        ChannelRecord(name="x", handle="@x", channel_url=channel_url)


def test_handle_must_be_at_prefixed():
    with pytest.raises(ValidationError):
        ChannelRecord(name="x", handle="x", channel_url="https://www.youtube.com/@x")


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        ChannelRecord(
            name="x",
            handle="@x",
            channel_url="https://www.youtube.com/@x",
            invented_field=True,
        )
