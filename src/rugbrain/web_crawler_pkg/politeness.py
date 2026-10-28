"""Politeness: per-domain rate limiting and a robots.txt cache.

Moved verbatim from ``web_crawler.py``.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

import httpx

from rugbrain.web_crawler_pkg.config import (
    CRAWLER_MIN_INTERVAL,
    CRAWLER_USER_AGENT,
    ROBOTS_TTL,
)

# ── Politeness helpers ───────────────────────────────────────────────────


class DomainRateLimiter:
    """Per-domain minimum-interval throttle shared across crawl workers."""

    def __init__(self, default_interval: float = CRAWLER_MIN_INTERVAL) -> None:
        self._interval = default_interval
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    @staticmethod
    def _domain(url: str) -> str:
        return (urlparse(url).netloc or url).lower()

    def _lock(self, domain: str) -> asyncio.Lock:
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    async def acquire(self, url: str) -> None:
        """Sleep so consecutive requests to *url*'s domain are spaced out."""
        domain = self._domain(url)
        async with self._lock(domain):
            wait = self._interval - (time.monotonic() - self._last.get(domain, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[domain] = time.monotonic()


class RobotsTxtCache:
    """Best-effort robots.txt cache for the HTTP fallback path."""

    def __init__(self, ttl: int = ROBOTS_TTL) -> None:
        self._ttl = ttl
        self._cache: dict[str, tuple[float, list[str]]] = {}
        self._user_agent = CRAWLER_USER_AGENT.split("/")[0]

    def _disallow(self, domain: str) -> list[str]:
        entry = self._cache.get(domain)
        if entry and time.monotonic() - entry[0] < self._ttl:
            return entry[1]
        return []

    async def allowed(self, url: str, client: httpx.AsyncClient) -> bool:
        """Return True when robots.txt for *url*'s domain allows crawling."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if not domain:
            return False
        if domain not in self._cache:
            rules: list[str] = []
            try:
                r = await client.get(
                    f"{parsed.scheme}://{domain}/robots.txt", timeout=10.0, follow_redirects=True
                )
                if r.status_code == 200:
                    rules = self._parse_disallow(r.text)
            except Exception:
                rules = []
            self._cache[domain] = (time.monotonic(), rules)
        for disallow in self._disallow(domain):
            if disallow and parsed.path.startswith(disallow):
                return False
        return True

    @staticmethod
    def _parse_disallow(text: str) -> list[str]:
        """Collect ``Disallow`` paths from the default (``*``) user-agent group."""
        paths: list[str] = []
        in_default_group = False
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                in_default_group = value in ("*", CRAWLER_USER_AGENT.split("/")[0])
            elif key == "disallow" and in_default_group and value:
                paths.append(value)
        return paths
