
from __future__ import annotations

import importlib as _il
import time
from typing import Any

import httpx
import pytest
import respx

web_crawler = _il.import_module("rugbrain.web_crawler")

mod = web_crawler

# ── Politeness helpers ───────────────────────────────────────────────────


async def test_domain_rate_limiter_spaces_requests() -> None:
    limiter = mod.DomainRateLimiter(default_interval=0.05)
    await limiter.acquire("https://a.com/x")
    t0 = time.monotonic()
    await limiter.acquire("https://a.com/x")
    assert time.monotonic() - t0 >= 0.04
    t1 = time.monotonic()
    await limiter.acquire("https://b.com/y")  # different domain → no wait
    assert time.monotonic() - t1 < 0.04


def test_robots_parse_disallow_default_group() -> None:
    text = "User-agent: *\nDisallow: /search\nDisallow: /api\n\nUser-agent: googlebot\nDisallow: /x"
    assert mod.RobotsTxtCache._parse_disallow(text) == ["/search", "/api"]


def test_robots_parse_ignores_other_agents() -> None:
    text = "User-agent: googlebot\nDisallow: /x"
    assert mod.RobotsTxtCache._parse_disallow(text) == []


@respx.mock
async def test_robots_allowed_respects_disallow() -> None:
    respx.get("https://example.com/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /private\n"
    )
    cache = mod.RobotsTxtCache(ttl=60)
    async with httpx.AsyncClient() as client:
        assert await cache.allowed("https://example.com/public/x", client) is True
        assert await cache.allowed("https://example.com/private/y", client) is False


def test_html_to_text_strips_noise() -> None:
    html = "<html><script>evil()</script><nav>menu</nav><p>Hello   <b>world</b></p></html>"
    text = mod.html_to_text(html)
    assert "Hello world" in text
    assert "evil()" not in text
    assert "menu" not in text


# ── WebCrawler ───────────────────────────────────────────────────────────


def test_crawl4ai_probe_never_raises() -> None:
    c = mod.WebCrawler()
    result = c._crawl4ai()
    assert result is None or hasattr(result, "AsyncWebCrawler")


async def test_crawl_http_fallback_extracts_text(monkeypatch: pytest.MonkeyPatch) -> Any:
    c = mod.WebCrawler()

    async def fake_httpx(url: str) -> Any:
        # _httpx_fetch normally runs html_to_text before returning.
        return mod.html_to_text(
            "<html><p>Clean <b>content</b> here.</p><script>x()</script></html>"
        )

    # Prevent crawl4ai from running (it is installed system-wide).
    async def fake_crawl4ai(url: str) -> str | None:
        return None

    monkeypatch.setattr(c, "_crawl4ai_fetch", fake_crawl4ai)
    monkeypatch.setattr(c, "_httpx_fetch", fake_httpx)
    doc = await c.crawl("https://example.com/story/1", source="test", collection="c")
    assert "Clean content here" in doc.content
    assert doc.source == "test"
    assert doc.collection == "c"


async def test_crawl_http_fallback_empty_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    c = mod.WebCrawler()

    async def fake_httpx(url: str) -> Any:
        return None

    # Prevent crawl4ai from running (it is installed system-wide).
    async def fake_crawl4ai(url: str) -> str | None:
        return None

    monkeypatch.setattr(c, "_crawl4ai_fetch", fake_crawl4ai)
    monkeypatch.setattr(c, "_httpx_fetch", fake_httpx)
    doc = await c.crawl("https://example.com/blocked", source="test", collection="c")
    assert doc.content == ""


async def test_crawl_many_bounded_concurrency(monkeypatch: pytest.MonkeyPatch) -> Any:
    c = mod.WebCrawler()
    calls: list[str] = []

    async def fake_httpx(url: str) -> Any:
        calls.append(url)
        return f"content for {url}"

    monkeypatch.setattr(c, "_httpx_fetch", fake_httpx)
    docs = await c.crawl_many(
        ["https://a.com/1", "https://a.com/2", "https://a.com/3"],
        source="s",
        collection="c",
        limit=2,
        concurrency=2,
    )
    assert len(docs) == 2
    assert len(calls) == 2


# ── Entry points (store mocked to avoid network) ─────────────────────────


def _fake_store_fn(stored: list[list[Any]]) -> Any:
    async def fake_store(docs: list[Any], collection: str) -> dict[str, int]:
        stored.append(docs)
        return {"inserted": len(docs), "failed": 0, "chunks": len(docs)}

    return fake_store


async def test_crawl_crypto_news_rss_first(monkeypatch: pytest.MonkeyPatch) -> Any:
    c = mod.WebCrawler()
    stored: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(c, "store", _fake_store_fn(stored))

    async def fake_fetch_rss(
        url: Any,
        source: Any,
        collection: Any,
        category: Any,
        limit: int = 15,
    ) -> Any:
        return [
            mod.doc_from_article(
                title=f"{source} story",
                content="body text",
                url=url,
                source=source,
                collection=collection,
                category=category,
            )
        ]

    monkeypatch.setattr(mod, "fetch_rss_feed", fake_fetch_rss)
    result = await mod.crawl_crypto_news(c, limit=30)
    assert result["inserted"] == len(mod.NEWS_FEEDS)
    assert stored and stored[0]


async def test_crawl_crypto_news_homepage_fallback(monkeypatch: pytest.MonkeyPatch) -> Any:
    c = mod.WebCrawler()
    stored: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(c, "store", _fake_store_fn(stored))

    async def fake_feed_empty(*a: Any, **k: Any) -> Any:
        return []

    monkeypatch.setattr(mod, "fetch_rss_feed", fake_feed_empty)

    async def fake_httpx(url: str) -> Any:
        return "homepage crawl content"

    monkeypatch.setattr(c, "_httpx_fetch", fake_httpx)
    result = await mod.crawl_crypto_news(c, limit=30)
    assert result["inserted"] == len(mod.NEWS_FEEDS)


async def test_crawl_security_feeds(monkeypatch: pytest.MonkeyPatch) -> Any:
    c = mod.WebCrawler()
    stored: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(c, "store", _fake_store_fn(stored))

    async def fake_httpx(url: str) -> Any:
        return "security blog content"

    monkeypatch.setattr(c, "_httpx_fetch", fake_httpx)
    result = await mod.crawl_security_feeds(c, limit=20)
    assert result["inserted"] == len(mod.SECURITY_BLOGS)


@respx.mock
async def test_crawl_social_sentiment_via_searxng(monkeypatch: pytest.MonkeyPatch) -> None:
    c = mod.WebCrawler()
    stored: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(c, "store", _fake_store_fn(stored))
    monkeypatch.setattr(mod, "SEARXNG_URL", "http://127.0.0.1:8089")

    respx.get(url__regex=r"http://127\.0\.0\.1:8089/search.*").respond(
        200,
        json={
            "results": [
                {
                    "title": "Crypto tweet title",
                    "url": "https://x.com/handle/status/1",
                    "content": "gm",
                }
            ]
        },
    )
    result = await mod.crawl_social_sentiment(c, limit=30)
    assert result["inserted"] == len(mod.X_ACCOUNTS)
    assert stored and stored[0][0].source.startswith("x:")


@respx.mock
async def test_crawl_github_audits(monkeypatch: pytest.MonkeyPatch) -> None:
    c = mod.WebCrawler()
    stored: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(c, "store", _fake_store_fn(stored))

    respx.get(url__regex=r"https://api\.github\.com/search/repositories.*").respond(
        200,
        json={
            "items": [
                {
                    "full_name": "org/audit-repo",
                    "description": "Audit report repo",
                    "html_url": "https://github.com/org/audit-repo",
                    "stargazers_count": 10,
                    "updated_at": "2026-08-01T00:00:00Z",
                }
            ]
        },
    )
    respx.get("https://api.github.com/repos/org/audit-repo/readme").respond(
        200, text="# Audit Report\n\nFindings and PoC."
    )
    result = await mod.crawl_github_audits(c, limit=15)
    assert result["inserted"] == 1
    assert stored and stored[0][0].metadata["repo"] == "org/audit-repo"


@respx.mock
async def test_fetch_rss_feed_parses_items() -> None:
    respx.get("https://feed.example.com/rss").respond(
        200,
        text="""<rss><channel><title>F</title>
        <item><title>Hello</title><link>https://feed.example.com/a</link>
        <description>Summary text</description></item></channel></rss>""",
    )
    docs = await mod.fetch_rss_feed("https://feed.example.com/rss", "feed", "c", "cat", limit=10)
    assert len(docs) == 1
    assert docs[0].title == "Hello"
    assert "Summary text" in docs[0].content
