"""GetXAPI HTTP client — correct paths (no /v1), budget-gated, fail-open.

Live-verified spec (2026-09-18):
* Base ``https://api.getxapi.com`` — the legacy ``.../v1`` suffix 404s.
* Auth: ``Authorization: Bearer <key>``.
* Endpoints: ``/twitter/user/tweets?userName=&count=`` (~20/page),
  ``/twitter/user/info?userName=``, ``/twitter/user/mentions?userName=``,
  ``/twitter/tweet/advanced_search?q=&queryType=Latest``,
  ``/twitter/tweet/detail``, ``/twitter/trends?woeid=1``,
  ``/twitter/user/status?userName=``, ``/account/me`` (free — credits).

Every fetcher: Redis cache first (cached hit = zero calls), then
``can_afford_call()`` gate, then HTTP, then ``record_call()`` metering.
Any failure → ``None`` / ``[]`` with a log line.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import httpx

from rugbrain.ingest_sources.x_intel.budget import BudgetTracker, default_tracker
from rugbrain.ingest_sources.x_intel.config import (
    CACHE_TRENDS_S,
    CACHE_TWEETS_S,
    CACHE_USER_INFO_S,
    GETXAPI_BASE_URL,
    GETXAPI_TIMEOUT_S,
    resolve_api_key,
)
from rugbrain.ingest_sources.x_intel.normalize import parse_tweets_payload

logger = logging.getLogger(__name__)


def _cache_key(prefix: str, *parts: str) -> str:
    joined = ":".join(str(p).strip().lower() for p in parts if str(p).strip())
    return f"xintel:getxapi:{prefix}:{joined}"


class GetXAPIClient:
    """Async GetXAPI client. ``transport`` is injectable for tests."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        tracker: BudgetTracker | None = None,
    ) -> None:
        self._transport = transport
        self._tracker = tracker or default_tracker()

    def _headers(self) -> dict[str, str]:
        key = resolve_api_key()
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def _cache_get(self, key: str) -> Any | None:
        try:
            from rugbrain.compat import get_redis_async

            raw = await (await get_redis_async()).get(key)
            return json.loads(raw) if raw is not None else None
        except Exception:
            logger.debug("xintel:cache_read_failed key=%s", key)
            return None

    async def _cache_set(self, key: str, value: Any, ttl: float) -> None:
        try:
            from rugbrain.compat import get_redis_async

            await (await get_redis_async()).set(key, json.dumps(value), ex=int(ttl))
        except Exception:
            logger.debug("xintel:cache_write_failed key=%s", key)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any | None:
        """One authenticated GET; budget-gated, metered, fail-open."""
        if not resolve_api_key():
            logger.debug("xintel:get_skipped_no_key path=%s", path)
            return None
        if not await self._tracker.can_afford_call():
            logger.debug("xintel:get_skipped_budget path=%s", path)
            return None
        try:
            async with httpx.AsyncClient(
                timeout=GETXAPI_TIMEOUT_S, transport=self._transport
            ) as client:
                r = await client.get(
                    f"{GETXAPI_BASE_URL}{path}", params=params, headers=self._headers()
                )
                await self._tracker.record_call(endpoint=path)
                if r.status_code != 200:
                    logger.warning("xintel:http status=%s path=%s", r.status_code, path)
                    return None
                return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("xintel:get_failed path=%s err=%s", path, str(exc)[:160])
            return None

    async def user_tweets(self, handle: str, count: int = 20) -> list[dict[str, Any]]:
        """GET /twitter/user/tweets — cached 15 min, fail-open []."""
        handle = str(handle or "").strip().lstrip("@")
        if not handle:
            return []
        key = _cache_key("user_tweets", handle, str(count))
        cached = await self._cache_get(key)
        if cached is not None:
            return cast("list[dict[str, Any]]", cached)
        payload = await self._get(
            "/twitter/user/tweets", {"userName": handle, "count": max(1, min(int(count), 100))}
        )
        tweets = parse_tweets_payload(payload)[: max(1, min(int(count), 100))]
        await self._cache_set(key, tweets, CACHE_TWEETS_S)
        return tweets

    async def user_info(self, handle: str) -> dict[str, Any] | None:
        """GET /twitter/user/info — cached 1 hour, fail-open None."""
        handle = str(handle or "").strip().lstrip("@")
        if not handle:
            return None
        key = _cache_key("user_info", handle)
        cached = await self._cache_get(key)
        if cached is not None:
            return cast("dict[str, Any]", cached)
        payload = await self._get("/twitter/user/info", {"userName": handle})
        if isinstance(payload, dict):
            await self._cache_set(key, payload, CACHE_USER_INFO_S)
        return payload if isinstance(payload, dict) else None

    async def user_mentions(self, handle: str, count: int = 20) -> list[dict[str, Any]]:
        """GET /twitter/user/mentions — cached 15 min, fail-open []."""
        handle = str(handle or "").strip().lstrip("@")
        if not handle:
            return []
        key = _cache_key("user_mentions", handle, str(count))
        cached = await self._cache_get(key)
        if cached is not None:
            return cast("list[dict[str, Any]]", cached)
        payload = await self._get(
            "/twitter/user/mentions", {"userName": handle, "count": max(1, min(int(count), 100))}
        )
        tweets = parse_tweets_payload(payload)[: max(1, min(int(count), 100))]
        await self._cache_set(key, tweets, CACHE_TWEETS_S)
        return tweets

    async def advanced_search(self, query: str, count: int = 20) -> list[dict[str, Any]]:
        """GET /twitter/tweet/advanced_search (queryType=Latest) — cached 15 min."""
        query = str(query or "").strip()
        if not query:
            return []
        key = _cache_key("search", query, str(count))
        cached = await self._cache_get(key)
        if cached is not None:
            return cast("list[dict[str, Any]]", cached)
        payload = await self._get(
            "/twitter/tweet/advanced_search",
            {"q": query, "queryType": "Latest", "count": max(1, min(int(count), 100))},
        )
        tweets = parse_tweets_payload(payload)[: max(1, min(int(count), 100))]
        await self._cache_set(key, tweets, CACHE_TWEETS_S)
        return tweets

    async def trends(self, woeid: int = 1) -> list[dict[str, Any]]:
        """GET /twitter/trends?woeid= — cached 30 min, fail-open []."""
        key = _cache_key("trends", str(woeid))
        cached = await self._cache_get(key)
        if cached is not None:
            return cast("list[dict[str, Any]]", cached)
        payload = await self._get("/twitter/trends", {"woeid": woeid})
        trends = payload if isinstance(payload, list) else []
        if not trends and isinstance(payload, dict):
            for k in ("data", "trends", "result"):
                if isinstance(payload.get(k), list):
                    trends = payload[k]
                    break
        await self._cache_set(key, trends, CACHE_TRENDS_S)
        return trends

    async def account_me(self) -> dict[str, Any] | None:
        """GET /account/me — FREE (no budget gate); credits dashboard."""
        payload = await self._get("/account/me")
        return payload if isinstance(payload, dict) else None


__all__ = ["GetXAPIClient"]
