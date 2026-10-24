"""The bots/launchpads manifest is the corpus provenance contract.

These tests are network-free: they prove the manifest is well formed, unique,
and that a page normalises into an ``IngestDoc`` carrying every required
provenance field (url, publisher, date, licence, doc_type, canonical id).
"""

from __future__ import annotations

import json

import pytest

from rugbrain.ingest_sources.bot_launchpad_docs import (
    SOURCE_NAME,
    BotLaunchpadDocsSource,
    sitemap_urls,
)
from rugbrain.ingest_sources.docs_manifest import canonical_id, load_manifest, manifest_path

ENTRIES = load_manifest()
REQUIRED_METADATA = ("publisher", "category", "doc_type", "license", "retrieved_on", "canonical_id")


def test_manifest_is_not_empty_and_ids_are_unique() -> None:
    assert len(ENTRIES) >= 20
    ids = [e.id for e in ENTRIES]
    assert len(ids) == len(set(ids))


def test_every_entry_declares_provenance() -> None:
    for entry in ENTRIES:
        assert entry.publisher and entry.license and entry.retrieved_on
        assert entry.doc_type in {"bot_docs", "launchpad_docs", "whitepaper"}
        assert entry.url.startswith("https://")
        assert entry.max_pages >= 1


def test_blocked_sources_are_recorded_with_a_reason() -> None:
    data = json.loads(manifest_path().read_text(encoding="utf-8"))
    blocked = data["blocked"]
    assert blocked, "an unreadable source must be recorded, not silently dropped"
    for row in blocked:
        assert row["publisher"] and row["reason"]


def test_duplicate_id_is_rejected(tmp_path) -> None:
    row = {
        "id": "dup",
        "publisher": "p",
        "doc_type": "bot_docs",
        "license": "x",
        "retrieved_on": "2026-10-25",
        "mode": "document",
        "url": "https://example.com/",
    }
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"entries": [row, row]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_manifest(path)


def test_non_https_url_is_rejected(tmp_path) -> None:
    row = {
        "id": "http-only",
        "publisher": "p",
        "doc_type": "bot_docs",
        "license": "x",
        "retrieved_on": "2026-10-25",
        "mode": "document",
        "url": "http://example.com/",
    }
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"entries": [row]}), encoding="utf-8")
    with pytest.raises(ValueError, match="https"):
        load_manifest(path)


def test_canonical_id_is_stable_and_scoped() -> None:
    entry = next(e for e in ENTRIES if e.mode == "sitemap")
    assert canonical_id(entry) == entry.id
    page = canonical_id(entry, "https://docs.example.com/fees/bonding-curve/")
    assert page.startswith(entry.id + ":"), page
    assert canonical_id(entry, "https://docs.example.com/fees/bonding-curve/") == page


def test_sitemap_urls_are_capped_and_skip_indexes() -> None:
    xml = (
        "<urlset><url><loc>https://a.example/1</loc></url>"
        "<url><loc>https://a.example/2</loc></url>"
        "<url><loc>https://a.example/sitemap.xml</loc></url>"
        "<url><loc>http://insecure.example/3</loc></url></urlset>"
    )
    assert sitemap_urls(xml, 5) == ["https://a.example/1", "https://a.example/2"]
    assert sitemap_urls(xml, 1) == ["https://a.example/1"]


def test_source_declares_the_4096_collection() -> None:
    source = BotLaunchpadDocsSource()
    assert source.name == SOURCE_NAME
    assert source.default_collection == "research_v2"
