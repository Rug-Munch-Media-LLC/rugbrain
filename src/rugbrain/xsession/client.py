"""Session-cookie transport for X's GraphQL API.

One request per second, hard. A 429 is honoured by waiting out
``x-rate-limit-reset`` (capped) and retrying at most ``MAX_429_RETRIES``
times. 401/403 triggers exactly one cookie reload from disk; if that fails the
guest lane is tried once and the error is surfaced — never swallowed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from rugbrain.xsession import config
from rugbrain.xsession.cookies import cookie_header, load_session_cookies
from rugbrain.xsession.guest import activate_guest

logger = logging.getLogger(__name__)


class XAuthError(RuntimeError):
    """The session is not authenticated and no fallback lane is available."""


class XSessionClient:
    """Authenticated GraphQL client with per-endpoint rate-limit tracking."""

    def __init__(self, cookie_path: str | None = None, rps: float | None = None) -> None:
        self._cookie_path = cookie_path or config.COOKIE_PATH
        self._interval = 1.0 / max(0.1, rps or config.REQUESTS_PER_SECOND)
        self._last_call = 0.0
        self._cookies = load_session_cookies(self._cookie_path)
        self._guest = False
        self._http: httpx.AsyncClient | None = None
        #: last-seen rate-limit state per operation, for the run report
        self.rate: dict[str, dict[str, int]] = {}

    async def __aenter__(self) -> "XSessionClient":
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=8.0))
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _headers(self) -> dict[str, str]:
        base = {
            "authorization": f"Bearer {config.PUBLIC_BEARER}",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "content-type": "application/json",
            "accept": "*/*",
            "user-agent": config.USER_AGENT,
        }
        if self._guest:
            base.pop("x-twitter-auth-type", None)
            base["x-guest-token"] = self._guest_token
            return base
        base["x-csrf-token"] = self._cookies["ct0"]
        base["Cookie"] = cookie_header(self._cookies)
        return base

    async def _throttle(self) -> None:
        wait = self._interval - (time.monotonic() - self._last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    def _absorb(self, operation: str, response: httpx.Response) -> None:
        try:
            self.rate[operation] = {
                "limit": int(response.headers.get("x-rate-limit-limit") or 0),
                "remaining": int(response.headers.get("x-rate-limit-remaining") or 0),
                "reset": int(response.headers.get("x-rate-limit-reset") or 0),
            }
        except (TypeError, ValueError):
            return

    async def _recover(self, response: httpx.Response) -> bool:
        """401/403 path: reload the jar once, else activate a guest token."""
        if self._guest:
            return False
        logger.warning("auth rejected (http=%s) — reloading cookie jar", response.status_code)
        try:
            self._cookies = load_session_cookies(self._cookie_path)
            return True
        except Exception as exc:  # noqa: BLE001 - jar may be gone; fall through to guest
            logger.warning("cookie reload failed: %s", type(exc).__name__)
        if self._http is not None:
            headers = await activate_guest(self._http, self._cookies.get("ct0", ""))
            if headers:
                self._guest = True
                self._guest_token = headers.get("x-guest-token", "")
                return True
        return False

    async def graphql(self, operation: str, query_id: str, params: dict[str, str]) -> dict[str, Any]:
        """Primary entry point: one GraphQL GET with throttle, backoff and recovery."""
        if self._http is None:
            raise RuntimeError("client used outside 'async with'")
        url = f"{config.GRAPHQL_BASE}/{query_id}/{operation}"
        recovered = False
        for attempt in range(config.MAX_429_RETRIES + 1):
            await self._throttle()
            response = await self._http.get(url, params=params, headers=self._headers())
            self._absorb(operation, response)
            if response.status_code == 200:
                return response.json()
            if response.status_code == 429:
                await asyncio.sleep(self._backoff(operation, attempt))
                continue
            if response.status_code in (401, 403) and not recovered:
                recovered = await self._recover(response)
                if recovered:
                    continue
            detail = response.text[:160] or "<empty body>"
            raise XAuthError(f"{operation}: http={response.status_code} body={detail}")
        raise XAuthError(f"{operation}: rate limited after {config.MAX_429_RETRIES} retries")

    def _backoff(self, operation: str, attempt: int) -> float:
        reset = self.rate.get(operation, {}).get("reset") or 0
        if reset:
            return float(min(60, max(2, reset - int(time.time()) + 1)))
        return float(5 * (2**attempt))
