"""SearXNG fallback poller for fresh crypto security alerts."""

from __future__ import annotations

import logging

import httpx

from . import config
from .models import SecurityAlert
from .vectors import detect_vector

log = logging.getLogger("rugbrain.alerts")


async def _searxng_fallback(client: httpx.AsyncClient, now: float) -> list[SecurityAlert]:
    """Fallback: query SearXNG (local, port 8089) for fresh crypto security alerts."""
    alerts: list[SecurityAlert] = []
    try:
        r = await client.get(
            f"{config.SEARXNG_URL}/search",
            params={
                "q": "crypto hack exploit alert",
                "format": "json",
                "safesearch": "0",
                "time_range": "day",
            },
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code != 200:
            log.debug("searxng_fallback_unavailable status=%d", r.status_code)
            return alerts
        results = (r.json() or {}).get("results", []) or []
        for res in results[:10]:
            if not isinstance(res, dict):
                continue
            title = str(res.get("title", "")).strip()
            url = str(res.get("url", "")).strip()
            content = str(res.get("content", "")).strip()
            if not title:
                continue
            vector = detect_vector(f"{title} {content}")
            alerts.append(
                SecurityAlert(
                    source="searxng",
                    title=title[:220],
                    description=content[:800],
                    url=url,
                    severity="high",
                    attack_vector=vector,
                    chain="unknown",
                    protocol="",
                    amount_lost_usd=0.0,
                    date="",
                    raw={"engine": "searxng"},
                )
            )
    except Exception as e:
        log.debug("searxng_fallback_error: %s", e)
    return alerts
