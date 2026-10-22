"""Guest-token lane — fallback only.

The authenticated session cookies are the primary lane. Guest tokens are used
solely when the session lane answers 401/403 *and* the jar cannot be
refreshed. X has been retiring this endpoint (it answers 404 code 34 on some
edges); that is treated as "guest lane unavailable", never as a crash.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from rugbrain.xsession import config

logger = logging.getLogger(__name__)


def guest_headers(token: str, csrf: str, csrf_token: str = "") -> dict[str, str]:
    return {
        "authorization": f"Bearer {config.PUBLIC_BEARER}",
        "x-guest-token": token,
        "x-csrf-token": csrf_token or csrf,
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
        "content-type": "application/json",
        "user-agent": config.USER_AGENT,
    }


async def activate_guest(client: httpx.AsyncClient, csrf: str) -> dict[str, str] | None:
    """Primary entry point: activate a guest token, or ``None`` if unavailable."""
    try:
        response = await client.post(
            config.ACTIVATE_URL,
            headers={
                "authorization": f"Bearer {config.PUBLIC_BEARER}",
                "content-type": "application/json",
                "user-agent": config.USER_AGENT,
            },
            content=b"",
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("guest activate transport error: %s", type(exc).__name__)
        return None

    if response.status_code != 200:
        detail: Any = response.text[:120]
        logger.warning("guest lane unavailable: http=%s body=%s", response.status_code, detail)
        return None

    try:
        token = (response.json() or {}).get("guest_token")
    except ValueError:
        logger.warning("guest activate returned non-JSON")
        return None
    if not token:
        logger.warning("guest activate returned no token")
        return None
    logger.info("guest token activated (fallback lane)")
    return guest_headers(str(token), csrf)
