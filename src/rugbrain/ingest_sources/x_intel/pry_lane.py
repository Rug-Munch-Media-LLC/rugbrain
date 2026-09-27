"""Pry scrape lane — FREE fallback for X content (saves the GetXAPI budget).

Ported from rugmunchbot ``x_ingest`` Pry helpers + ``support/pry_auth.py``.
Pry is self-hosted at ``http://127.0.0.1:8005``; ``/v1/scrape`` is x402-gated
and unlocked with the internal ``PRY_API_KEY`` Bearer credential, or with the
first-party internal credential (see ``pry_internal_auth``).

Helpers: ``scrape_tweet`` (one tweet URL → tweet dict, cached 15 min) and
``scrape_profile`` (profile page → bio/followers/recent tweets, cached 1 h).
Markdown parsing lives in ``pry_parse``. Fail-open: any error → ``None``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx

from rugbrain.ingest_sources.x_intel import pry_internal_auth
from rugbrain.ingest_sources.x_intel.pry_cache import cache_get, cache_set
from rugbrain.ingest_sources.x_intel.pry_parse import (
    extract_created_at,
    extract_engagement,
    extract_tweet_text,
    handle_from_url,
    tweet_id_from_url,
)
from rugbrain.ingest_sources.x_intel.pry_paywall import (
    PRY_402_COUNTER_KEY,
    PRY_LANE_EXIT_CODE,
    PryLaneUnavailable,
    record_paywall,
)

logger = logging.getLogger(__name__)

PRY_BASE_URL: str = os.getenv("PRYSCRAPER_URL", "http://127.0.0.1:8005").rstrip("/")
PRY_SCRAPE_TIMEOUT_S: float = float(os.getenv("PRY_SCRAPE_TIMEOUT_S", "45"))
PRY_CACHE_TWEETS_S: float = float(os.getenv("XINTEL_PRY_CACHE_TWEETS_S", "900"))
PRY_CACHE_PROFILE_S: float = float(os.getenv("XINTEL_PRY_CACHE_PROFILE_S", "3600"))
ScrapeFn = Callable[..., Awaitable[dict[str, Any] | None]]


def pry_headers(
    method: str = "POST", path: str = pry_internal_auth.PRY_SCRAPE_PATH
) -> dict[str, str]:
    """Auth headers for one Pry request.

    Bearer key when configured, plus Pry's first-party internal credential
    (``X-Internal-Token`` / ``X-Internal-Signature`` / ``X-Internal-Timestamp``)
    so first-party jobs skip the x402 challenge instead of paying us.

    The internal triple is minted per call: the signature covers the timestamp
    and Pry refuses a signature it has already seen, so cached headers would
    replay-fail. Omitted entirely when ``PRY_INTERNAL_TOKEN`` is unset.
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = os.getenv("PRY_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if os.getenv("PRY_X402_OFFLINE", "false").lower() in ("1", "true", "yes"):
        headers["X-X402-Offline"] = "true"
    headers.update(pry_internal_auth.pry_internal_headers(method=method, path=path))
    return headers


async def _pry_scrape(
    url: str, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any] | None:
    """POST *url* to Pry ``/v1/scrape`` (Cloudflare bypass + JS render).

    Transient failures (Pry down, timeout, 5xx) stay fail-open → ``None``.
    An x402 402 is NOT transient: it raises ``PryLaneUnavailable`` so the
    cycle can fail loudly instead of silently scraping nothing forever.
    """
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=PRY_SCRAPE_TIMEOUT_S, transport=transport) as client:
            response = await client.post(
                f"{PRY_BASE_URL}{pry_internal_auth.PRY_SCRAPE_PATH}",
                headers=pry_headers("POST", pry_internal_auth.PRY_SCRAPE_PATH),
                json={"url": url, "bypassCloudflare": True, "jsRender": True},
            )
    except Exception as exc:
        logger.debug("xintel:pry_scrape_failed url=%s err=%s", url, str(exc)[:160])
        return None
    if response.status_code == 402:
        await record_paywall()
        raise PryLaneUnavailable(
            f"pry /v1/scrape returned 402 (payment required) for url={url[:120]}"
        )
    try:
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        logger.debug("xintel:pry_scrape_failed url=%s err=%s", url, str(exc)[:160])
        return None
    if not isinstance(body, dict):
        return None
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return data if isinstance(data, dict) else None


async def scrape_tweet(
    tweet_url: str, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any] | None:
    """Scrape one X tweet via Pry (FREE) → GetXAPI-normalized tweet dict."""
    tweet_url = str(tweet_url or "").strip()
    tweet_id = tweet_id_from_url(tweet_url)
    if not tweet_id:
        return None
    cache_key = f"xintel:pry:tweet:{tweet_id}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cast("dict[str, Any]", cached)
    data = await _pry_scrape(tweet_url, transport)
    if not data:
        return None
    markdown = str(data.get("markdown") or data.get("content") or "").strip()
    if not markdown:
        return None
    text = extract_tweet_text(markdown)
    if len(text) < 8:
        return None
    handle = handle_from_url(tweet_url)
    result: dict[str, Any] = {
        "tweet_id": tweet_id,
        "text": text,
        "handle": handle,
        "author_name": str((data.get("metadata") or {}).get("title") or "").strip() or handle,
        "created_at": extract_created_at(markdown),
        **extract_engagement(markdown),
        "quote_count": 0,
        "url": tweet_url,
        "source": "pry",
    }
    await cache_set(cache_key, result, PRY_CACHE_TWEETS_S)
    return result


# Re-exported so the historical ``pry_lane.scrape_profile`` import path
# (pipeline, tests) keeps working after the profile scrape moved to its module.
from rugbrain.ingest_sources.x_intel.pry_profile import scrape_profile  # noqa: E402

__all__ = [
    "PRY_402_COUNTER_KEY",
    "PRY_BASE_URL",
    "PRY_LANE_EXIT_CODE",
    "PryLaneUnavailable",
    "pry_headers",
    "record_paywall",
    "scrape_profile",
    "scrape_tweet",
]
