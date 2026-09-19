"""Rekt News leaderboard poller."""

from __future__ import annotations

import json
import logging
import re

import httpx

from . import config
from .dates import _parse_date_iso, _within_window
from .models import SecurityAlert
from .vectors import detect_chain, detect_vector

log = logging.getLogger("rugbrain.alerts")


async def poll_rekt(client: httpx.AsyncClient, now: float) -> list[SecurityAlert]:
    """Recent major exploits from the Rekt News leaderboard."""
    alerts: list[SecurityAlert] = []
    try:
        r = await client.get(config.REKT_LEADERBOARD_URL, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            log.warning("rekt_poll_failed status=%d", r.status_code)
            return alerts
        html = r.text
        m = re.search(r'__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', html, re.S)
        if not m:
            m = re.search(r"__NEXT_DATA__\">(.*?)</script>", html, re.S)
        if not m:
            log.warning("rekt_poll_no_next_data")
            return alerts
        data = json.loads(m.group(1))
        entries = data.get("props", {}).get("pageProps", {}).get("leaderboard", []) or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            slug = str(entry.get("slug", "")).strip()
            if not title:
                continue
            rekt = entry.get("rekt") or {}
            amount = float(rekt.get("amount") or 0)
            date_raw = rekt.get("date") or entry.get("date") or ""
            date_iso = _parse_date_iso(str(date_raw))
            if not _within_window(date_iso, now, config.RECENT_WINDOW_DAYS):
                continue
            tags = entry.get("tags") or []
            tags_text = ", ".join(str(t) for t in tags)
            excerpt = str(entry.get("excerpt", "")).strip()
            vector = detect_vector(f"{title} {tags_text} {excerpt}")
            chain = detect_chain(f"{title} {tags_text}")
            alerts.append(
                SecurityAlert(
                    source="rekt",
                    title=title,
                    description=f"{excerpt} Tags: {tags_text}."
                    if excerpt
                    else f"Tags: {tags_text}.",
                    url=f"https://rekt.news/{slug}/" if slug else config.REKT_LEADERBOARD_URL,
                    severity="critical"
                    if amount >= 10_000_000
                    else ("high" if amount else "medium"),
                    attack_vector=vector,
                    chain=chain,
                    protocol=title,
                    amount_lost_usd=amount,
                    date=date_iso,
                    raw={"slug": slug, "tags": tags},
                )
            )
    except Exception as e:
        log.warning("rekt_poll_error: %s", e)
    return alerts
