"""Tests for rmi_rag.sources_registry — additional data source pipelines."""

from __future__ import annotations

import respx

from rmi_rag import sources_registry as mod


def test_all_requested_sources_present():
    names = {s.name for s in mod.NEW_SOURCES}
    for expected in (
        "cryptopanic",
        "cmc_alexandria",
        "messari",
        "bankless",
        "the_defiant",
        "week_in_ethereum",
        "solana_compass",
        "eigenlayer",
        "eips_ethereum",
        "bitcoin_optech",
    ):
        assert expected in names, f"missing source: {expected}"


def test_get_source():
    assert mod.get_source("cryptopanic") is not None
    assert mod.get_source("does_not_exist") is None


async def test_fetch_source_rss(monkeypatch):
    cfg = mod.get_source("week_in_ethereum")
    assert cfg is not None and cfg.kind == "rss"

    async def fake_feed(url, source, collection, category, limit=15):
        return [
            mod.doc_from_article(
                title="WIE",
                content="weekly summary",
                url=url,
                source=source,
                collection=collection,
                category=category,
            )
        ]

    monkeypatch.setattr(mod, "fetch_rss_feed", fake_feed)
    docs = await mod.fetch_source(cfg, limit=10)
    assert docs and docs[0].source == "week_in_ethereum"
    assert docs[0].collection == cfg.collection


@respx.mock
async def test_fetch_source_cryptopanic_api(monkeypatch):
    monkeypatch.setenv("CRYPTOPANIC_API_TOKEN", "test-token")
    respx.get("https://cryptopanic.com/api/v1/posts/").respond(
        200,
        json={
            "results": [
                {
                    "title": "BTC rallies",
                    "url": "https://cryptopanic.com/news/1",
                    "published_at": "2026-08-04T10:00:00Z",
                    "domain": "coindesk.com",
                    "votes": {"positive": 5, "negative": 1},
                }
            ]
        },
    )
    cfg = mod.get_source("cryptopanic")
    assert cfg is not None and cfg.kind == "api"
    docs = await mod.fetch_source(cfg, limit=10)
    assert len(docs) == 1
    assert docs[0].source == "cryptopanic"
    assert docs[0].metadata["upvotes"] == 5


async def test_fetch_source_cryptopanic_no_token(monkeypatch):
    monkeypatch.delenv("CRYPTOPANIC_API_TOKEN", raising=False)
    cfg = mod.get_source("cryptopanic")
    assert cfg is not None
    docs = await mod.fetch_source(cfg, limit=10)
    assert docs == []


async def test_fetch_source_rss_or_crawl_fallback(monkeypatch):
    cfg = mod.get_source("bankless")
    assert cfg is not None and cfg.kind == "rss_or_crawl"
    monkeypatch.setattr(mod, "fetch_rss_feed", lambda *a, **k: [])
    c = mod.WebCrawler()

    async def fake_crawl(url, source="", category="", collection="default", published=""):
        return mod.doc_from_article(
            title="Bankless",
            content="archive",
            url=url,
            source=source,
            collection=collection,
            category=category,
        )

    monkeypatch.setattr(c, "crawl", fake_crawl)
    docs = await mod.fetch_source(cfg, crawler=c, limit=10)
    assert len(docs) == 1
    assert docs[0].url == cfg.urls[1]  # fell back to the archive URL


async def test_fetch_source_crawl(monkeypatch):
    cfg = mod.get_source("eips_ethereum")
    assert cfg is not None and cfg.kind == "crawl"
    c = mod.WebCrawler()

    async def fake_crawl(url, source="", category="", collection="default", published=""):
        return mod.doc_from_article(
            title="EIP",
            content="standards",
            url=url,
            source=source,
            collection=collection,
            category=category,
        )

    monkeypatch.setattr(c, "crawl", fake_crawl)
    docs = await mod.fetch_source(cfg, crawler=c, limit=10)
    assert len(docs) == 1
    assert docs[0].source == "eips_ethereum"


async def test_ingest_all_sources_dry_run(monkeypatch):
    c = mod.WebCrawler()

    async def fake_fetch(cfg, crawler=None, client=None, limit=15):
        return [
            mod.doc_from_article(
                title=cfg.name,
                content="x",
                url="u",
                source=cfg.name,
                collection=cfg.collection,
                category=cfg.category,
            )
        ]

    monkeypatch.setattr(mod, "fetch_source", fake_fetch)
    totals = await mod.ingest_all_sources(c, dry_run=True, limit=3)
    assert set(totals.keys()) == {s.name for s in mod.NEW_SOURCES}
    assert all(t["inserted"] == 1 for t in totals.values())
