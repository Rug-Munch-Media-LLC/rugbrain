"""SearXNG exploit-account poller (RSS, JSON fallback)."""

from __future__ import annotations

import logging

import httpx

from .config import EXPLOIT_ACCOUNTS, SEARXNG_URL
from .heuristics import _CRITICAL_KEYWORDS
from .models import CriticalAlert

log = logging.getLogger("rugbrain.realtime_alerts")


def _searxng_account_query(account: str) -> str:
    """Search query targeting one security account."""
    return f"from:{account}"


async def poll_searxng_exploits(
    client: httpx.AsyncClient,
    accounts: tuple[str, ...] | None = None,
) -> list[CriticalAlert]:
    """Poll the tracked security accounts via SearXNG (RSS, JSON fallback).

    Every account is queried for its most recent posts. Items whose text
    looks like an exploit become candidate alerts. Fails open — a dead
    SearXNG instance yields an empty list.
    """
    from rugbrain import realtime_alerts as _pkg

    accounts = accounts or EXPLOIT_ACCOUNTS
    candidates: list[CriticalAlert] = []
    for account in accounts:
        params = {
            "q": _searxng_account_query(account),
            "format": "rss",
            "safesearch": "0",
            "time_range": "day",
        }
        items: list[tuple[str, str, str, str]] = []
        try:
            r = await client.get(
                f"{SEARXNG_URL}/search",
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if r.status_code == 200 and r.text.strip() and "<item" in r.text:
                from rugbrain.rss_parser import parse_feed

                _feed_title, feed_items = parse_feed(r.text)
                items = [
                    (
                        str(it.get("title", "")),
                        str(it.get("url", "")),
                        str(it.get("published", "")),
                        str(it.get("summary", "")),
                    )
                    for it in feed_items
                ]
            else:
                # Some SearXNG builds disable RSS — fall back to JSON.
                r2 = await client.get(
                    f"{SEARXNG_URL}/search",
                    params={**params, "format": "json"},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if r2.status_code == 200:
                    results = (r2.json() or {}).get("results", []) or []
                    items = [
                        (
                            str(res.get("title", "")),
                            str(res.get("url", "")),
                            "",
                            str(res.get("content", "")),
                        )
                        for res in results
                        if isinstance(res, dict)
                    ]
        except Exception as e:
            log.debug("searxng_poll_failed account=%s err=%s", account, str(e)[:120])
            continue

        for title, url, published, summary in items:
            text = f"{title} {summary}"
            if not _pkg.is_exploit_candidate(text):
                continue
            candidates.append(
                _pkg.CriticalAlert.from_candidate(
                    title=f"[{account}] {title[:180]}",
                    description=summary[:800],
                    url=url,
                    source="searxng",
                    severity="critical"
                    if any(k in text.lower() for k in _CRITICAL_KEYWORDS)
                    else "high",
                    raw={"account": account, "published": published},
                )
            )
    return candidates
