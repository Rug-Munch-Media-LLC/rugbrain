"""Tests for the self-hosted Pry client.

The tests that matter are the ones proving the four ways callers previously got
Pry wrong, because each of those produced a silent empty result rather than an
error: a 402 read as "Pry is down", the wrong response key read as "the page
had no text", a hard-blocked site read as "no content", and a bare internal
token read as "auth is broken".

Live tests hit the real container and are skipped when it is not running, so
this file is honest on a host without Pry and strict on a host with it.
"""

from __future__ import annotations

import httpx
import pytest

from rugbrain.sources.pry_scraper import (
    DEFAULT_CONCURRENCY,
    DEFAULT_DELAY_SECONDS,
    PryError,
    scrape,
    scrape_many,
)

AUTH = {"Authorization": "Bearer test-key", "Content-Type": "application/json"}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- the four historical mis-reads, pinned ----------------------------------


@pytest.mark.asyncio
async def test_402_is_reported_as_payment_not_as_dead_service():
    """A 402 means the owner key was rejected. It must not read as unreachable."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"error": "payment required: send 1000 USDC"})

    with pytest.raises(PryError) as exc:
        await scrape("https://example.com", client=_client(handler))
    assert exc.value.args[0] == "payment"


@pytest.mark.asyncio
async def test_text_is_read_from_data_markdown():
    """The text is at data.markdown, not at content or top-level markdown.

    Two earlier callers read the wrong key and concluded the scrape was empty.
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"markdown": "hello"}})

    assert await scrape("https://example.com", client=_client(handler)) == "hello"


@pytest.mark.asyncio
async def test_hard_blocked_site_surfaces_the_tier_detail():
    """Pry names every anti-bot tier it tried. That text must survive."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "type": "/errors/scrape_failed",
                "detail": "direct_failed; cloudscraper_failed; flaresolverr_failed",
            },
        )

    with pytest.raises(PryError) as exc:
        await scrape("https://blocked.example", client=_client(handler))
    assert exc.value.args[0] == "blocked"
    assert "flaresolverr_failed" in exc.value.args[1]


@pytest.mark.asyncio
async def test_empty_render_is_distinguished_from_blocked():
    """Success with no text is a different failure from 'could not scrape'."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"markdown": "  "}})

    with pytest.raises(PryError) as exc:
        await scrape("https://blank.example", client=_client(handler))
    assert exc.value.args[0] == "empty"


@pytest.mark.asyncio
async def test_scrape_many_records_failure_per_url_and_does_not_abort():
    """A 250-channel sweep must survive the first Cloudflare wall."""

    async def handler(request: httpx.Request) -> httpx.Response:
        url = request.read().decode()
        if "blocked" in url:
            return httpx.Response(
                422, json={"type": "/errors/scrape_failed", "detail": "tor_failed"}
            )
        return httpx.Response(200, json={"success": True, "data": {"markdown": f"ok {url[-8:]}"}})

    async with _client(handler) as client:
        out = await scrape_many(
            ["https://a.example", "https://blocked.example", "https://c.example"],
            client=client,
            delay=0,
        )
    assert out["https://blocked.example"].startswith("blocked:")
    assert "ok" in out["https://a.example"]
    assert "ok" in out["https://c.example"]


def test_politeness_defaults_are_conservative():
    """Capacity is not the constraint; being a good citizen is."""
    assert DEFAULT_CONCURRENCY <= 4
    assert DEFAULT_DELAY_SECONDS >= 0.5


# --- live, only when Pry is actually up -------------------------------------


@pytest.mark.asyncio
async def test_live_pry_scrapes_real_content():
    """Prove the auth path works end to end against the running container."""
    import httpx as h

    try:
        health = await h.AsyncClient(timeout=5).get("http://127.0.0.1:8005/health")
        if health.status_code != 200:
            pytest.skip("pry not running")
    except Exception:
        pytest.skip("pry not running")

    text = await scrape("https://rekt.news/", wait_for=3000)
    assert len(text) > 200, "rekt.news returned too little to be real content"
    assert "rekt" in text.lower() or len(text) > 500
