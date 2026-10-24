"""Catalogue provenance is complete, unique and machine-checkable.

No network: these tests read the checked-in ``config/content_sources.json``.
A source that cannot name its licence or its verification date must fail here,
not at ingest time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rugbrain.ingest_sources.content_sources import load_catalog, parse_specs

CATALOG = Path(__file__).resolve().parents[1] / "config" / "content_sources.json"


def test_catalog_loads_and_covers_every_kind() -> None:
    specs = load_catalog()
    assert len(specs) >= 20
    assert {spec.kind for spec in specs} == {"news", "medium", "paragraph", "youtube"}


def test_every_source_carries_provenance() -> None:
    for spec in load_catalog():
        assert spec.publisher and spec.licence and spec.fetched_at
        assert spec.feed_url.startswith("https://")
        provenance = spec.provenance()
        assert provenance["source_id"] == spec.id
        assert provenance["licence"] == spec.licence
        assert provenance["publisher"] == spec.publisher
        assert provenance["fetched_at"] == spec.fetched_at


def test_ids_and_feeds_are_unique() -> None:
    specs = load_catalog()
    ids = [spec.id for spec in specs]
    feeds = [spec.feed_url for spec in specs]
    assert len(ids) == len(set(ids))
    assert len(feeds) == len(set(feeds))


def test_youtube_sources_declare_a_channel_id() -> None:
    for spec in load_catalog():
        if spec.kind == "youtube":
            assert spec.channel_id.startswith("UC")
            assert spec.channel_id in spec.feed_url


def test_inactive_rows_are_reported_but_not_enabled() -> None:
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert payload["inactive"] and payload["blocked"]
    enabled = load_catalog()
    with_inactive = load_catalog(include_inactive=True)
    assert len(with_inactive) == len(enabled) + len(payload["inactive"])
    enabled_ids = {spec.id for spec in enabled}
    assert not enabled_ids & {row["id"] for row in payload["blocked"]}


@pytest.mark.parametrize(
    "row",
    [
        {"id": "x", "kind": "news", "publisher": "P", "feed_url": "https://a/feed"},
        {
            "id": "x",
            "kind": "podcast",
            "publisher": "P",
            "feed_url": "https://a/feed",
            "licence": "l",
            "fetched_at": "t",
        },
        {
            "id": "x",
            "kind": "news",
            "publisher": "P",
            "feed_url": "http://a/feed",
            "licence": "l",
            "fetched_at": "t",
        },
    ],
)
def test_incomplete_or_unsafe_rows_are_rejected(row: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        parse_specs([row])


def test_duplicate_ids_are_rejected() -> None:
    row = {
        "id": "dup",
        "kind": "news",
        "publisher": "P",
        "feed_url": "https://a/feed",
        "licence": "l",
        "fetched_at": "t",
    }
    with pytest.raises(ValueError, match="duplicate"):
        parse_specs([row, {**row, "feed_url": "https://b/feed"}])
