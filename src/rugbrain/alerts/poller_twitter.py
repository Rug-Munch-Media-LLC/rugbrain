"""Crypto security X accounts via Nitter RSS (SearXNG fallback)."""

from __future__ import annotations

import logging

import httpx

from . import config
from .dates import _within_window
from .models import SecurityAlert
from .poller_searxng import _searxng_fallback
from .rss import _parse_rss_items
from .vectors import detect_vector

log = logging.getLogger("rugbrain.alerts")


async def poll_twitter(client: httpx.AsyncClient, now: float) -> list[SecurityAlert]:
    """Crypto security X accounts via Nitter RSS (SearXNG fallback).

    These accounts (CertiK, PeckShield, Beosin, SpreekAway) publish exploit
    and scam alerts as they happen. Nitter instances change frequently, so a
    failure here is logged and skipped — never fatal.
    """
    alerts: list[SecurityAlert] = []
    for account in config.SECURITY_ACCOUNTS:
        account = account.strip()
        if not account:
            continue
        rss_url = f"{config.NITTER_URL}/{account}/rss"
        try:
            r = await client.get(rss_url, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code != 200 or not r.text.strip():
                log.debug("nitter_empty account=%s status=%d", account, r.status_code)
                continue
            items = _parse_rss_items(r.text, account)
            for item in items:
                title, link, pub_date, summary = item
                if not _within_window(pub_date, now, config.RECENT_WINDOW_DAYS):
                    continue
                vector = detect_vector(f"{title} {summary}")
                alerts.append(
                    SecurityAlert(
                        source="twitter",
                        title=f"[{account}] {title[:180]}",
                        description=summary[:800],
                        url=link,
                        severity="high",
                        attack_vector=vector,
                        chain="unknown",
                        protocol="",
                        amount_lost_usd=0.0,
                        date=pub_date,
                        raw={"account": account},
                    )
                )
        except Exception as e:
            log.debug("nitter_fetch_failed account=%s err=%s", account, str(e)[:120])
    if not alerts:
        alerts = await _searxng_fallback(client, now)
    return alerts
