"""Unit tests for x_intel normalization, GetXAPI fail-open, and Pry fallback."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from rugbrain.ingest_sources.x_intel.budget import BudgetTracker
from rugbrain.ingest_sources.x_intel.client import GetXAPIClient
from rugbrain.ingest_sources.x_intel.normalize import normalize_profile, normalize_tweet
from rugbrain.ingest_sources.x_intel.pry_lane import pry_headers, scrape_tweet
from tests.test_x_intel_routing_budget import FakeRedis


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _ok_tweets() -> dict[str, Any]:
    return {
        "data": [
            {"id": "123", "text": "exploit drained 5M", "author": {"username": "peckshield"}},
            {"id": "456", "text": "market update", "user": {"screen_name": "tier10k"}},
        ]
    }


@pytest.mark.asyncio
async def test_normalize_tweet_flat_and_nested() -> None:
    norm = normalize_tweet({"id": "1", "text": "hello world", "author": {"username": "a"}})
    assert norm is not None
    assert norm["tweet_id"] == "1" and norm["handle"] == "a"
    assert norm["url"] == "https://x.com/a/status/1" and norm["source"] == "getxapi"
    nested = normalize_tweet({"tweet": {"id": "2", "text": "hi"}, "user": {"screen_name": "b"}})
    assert nested is not None and nested["tweet_id"] == "2"
    assert normalize_tweet({"id": "3"}) is None  # no text → hostile row skipped


@pytest.mark.asyncio
async def test_normalize_profile() -> None:
    prof = normalize_profile({"username": "zachxbt", "followers_count": "120000", "bio": "sec"})
    assert prof is not None
    assert prof["followers_count"] == 120000 and prof["source"] == "getxapi"
    assert normalize_profile({"no_handle": True}) is None


@pytest.mark.asyncio
async def test_client_extracts_tweets_from_payload() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "/twitter/user/tweets" in request.url.path
        assert request.url.params["userName"] == "peckshield"
        return httpx.Response(200, json=_ok_tweets())

    client = GetXAPIClient(transport=_transport(handler))
    tweets = await client.user_tweets("peckshield", count=10)
    assert [t["tweet_id"] for t in tweets] == ["123", "456"]
    assert tweets[0]["handle"] == "peckshield"


@pytest.mark.asyncio
async def test_client_fail_open_on_http_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = GetXAPIClient(transport=_transport(handler))
    assert await client.user_tweets("peckshield") == []
    assert await client.user_info("peckshield") is None


@pytest.mark.asyncio
async def test_client_fail_open_on_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = GetXAPIClient(transport=_transport(handler))
    assert await client.user_tweets("peckshield") == []


@pytest.mark.asyncio
async def test_client_skips_call_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GETXAPI_KEY", raising=False)
    monkeypatch.setenv("GETXAPI_KEY_FILE", "/nonexistent/keyfile")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    client = GetXAPIClient(transport=_transport(handler))
    assert await client.user_tweets("peckshield") == []
    assert calls == []  # no HTTP call made without a key


@pytest.mark.asyncio
async def test_client_budget_gate_blocks_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GETXAPI_KEY", "test-key")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    blocked = BudgetTracker(redis_client=FakeRedis({"xintel:spend": 999.0}))
    client = GetXAPIClient(transport=_transport(handler), tracker=blocked)
    assert await client.user_tweets("peckshield") == []
    assert calls == []  # budget exhausted → no HTTP call


@pytest.mark.asyncio
async def test_pry_scrape_tweet_parses_markdown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["jsRender"] is True
        return httpx.Response(
            200,
            json={
                "data": {
                    "markdown": "@peckshield\nExploit drained 5M from the vault\n2:30 PM · Sep 2, 2026\n1,234 Likes",
                    "metadata": {"title": "PeckShield"},
                }
            },
        )

    tweet = await scrape_tweet("https://x.com/peckshield/status/999", transport=_transport(handler))
    assert tweet is not None
    assert tweet["tweet_id"] == "999" and tweet["source"] == "pry"
    assert "drained 5M" in tweet["text"] and tweet["like_count"] == 1234


@pytest.mark.asyncio
async def test_pry_scrape_fail_open() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("pry down")

    assert await scrape_tweet("https://x.com/a/status/1", transport=_transport(handler)) is None
    assert await scrape_tweet("not-a-tweet-url") is None


def test_pry_headers_bearer() -> None:
    headers = pry_headers()
    assert headers["Content-Type"] == "application/json"
    assert "dangerouslySetHeader" not in headers
