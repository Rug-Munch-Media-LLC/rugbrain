"""X account verification via fxtwitter (no key) + official-site corroboration."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import httpx

API = "https://api.fxtwitter.com/{}"
SNOWFLAKE_EPOCH_MS = 1288834974657
HEADERS = {"User-Agent": "rugbrain-xregistry/1.0", "Accept": "text/html,application/json"}
LINK_RE = re.compile(r"(?:x|twitter)\.com/([A-Za-z0-9_]{2,15})")


def account_created_at(user_id: str | None) -> str | None:
    """X account creation time derived from the snowflake id (no extra call)."""
    try:
        ms = (int(user_id or 0) >> 22) + SNOWFLAKE_EPOCH_MS
    except (TypeError, ValueError):
        return None
    if ms <= SNOWFLAKE_EPOCH_MS:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _variants(handle: str) -> list[str]:
    base = handle.strip().lstrip("@")
    out = [base]
    for variant in (base.lower(), base[:1].upper() + base[1:], base.upper()):
        if variant not in out:
            out.append(variant)
    return out


async def _get_user(client: httpx.AsyncClient, handle: str) -> dict[str, Any]:
    try:
        resp = await client.get(API.format(handle), timeout=15.0)
    except httpx.HTTPError as exc:  # network noise, retried by caller
        return {"code": -1, "message": type(exc).__name__}
    if resp.status_code == 429:
        return {"code": 429, "message": "rate_limited"}
    try:
        return resp.json()
    except ValueError:
        return {"code": -2, "message": f"bad_json:{resp.status_code}"}


async def lookup(client: httpx.AsyncClient, handle: str) -> dict[str, Any]:
    """Resolve one handle. 404 is retried once after 1.5 s, then case variants."""
    attempts: list[str] = []
    last: dict[str, Any] = {"code": 404, "message": "User not found"}
    for index, variant in enumerate(_variants(handle)):
        payload = await _get_user(client, variant)
        attempts.append(f"{variant}:{payload.get('code')}")
        if payload.get("code") == 200 and payload.get("user"):
            user = payload["user"]
            return {
                "status": "ok",
                "screen_name": user.get("screen_name"),
                "name": user.get("name") or user.get("screen_name"),
                "followers": int(user.get("followers") or 0),
                "bio": (user.get("description") or "")[:400],
                "user_id": user.get("id"),
                "created_at": account_created_at(user.get("id")),
                "verified_badge": bool((user.get("verification") or {}).get("verified")),
                "probed_as": variant,
                "attempts": attempts,
            }
        last = payload
        if payload.get("code") == 429:
            await asyncio.sleep(5.0)
        elif payload.get("code") == 404 and index == 0:
            await asyncio.sleep(1.5)
    return {
        "status": "missing",
        "code": last.get("code"),
        "message": last.get("message"),
        "attempts": attempts,
    }


async def site_links(client: httpx.AsyncClient, url: str | None, handle: str) -> dict[str, Any]:
    """Official-site corroboration: does the brand's own page link this X handle?"""
    if not url:
        return {"url": None, "status": "n/a", "linked": None, "handles": []}
    try:
        resp = await client.get(url, timeout=20.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        return {"url": url, "status": f"error:{type(exc).__name__}", "linked": None, "handles": []}
    if resp.status_code >= 400:
        return {"url": url, "status": f"http_{resp.status_code}", "linked": None, "handles": []}
    handles = sorted({m.lower() for m in LINK_RE.findall(resp.text[:500_000])})
    return {
        "url": url,
        "status": "ok",
        "linked": handle.lower() in handles,
        "handles": handles[:15],
    }


async def verify_all(items: list[dict[str, Any]], concurrency: int = 4) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(client: httpx.AsyncClient, item: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            site = await site_links(client, item.get("site"), item["handle"])
            result = await lookup(client, item["handle"])
            await asyncio.sleep(0.2)
            result["site"] = site
            return {**item, "x": result, "verified_at": datetime.now(tz=timezone.utc).isoformat()}

    async with httpx.AsyncClient(headers=HEADERS) as client:
        return await asyncio.gather(*(one(client, item) for item in items))
