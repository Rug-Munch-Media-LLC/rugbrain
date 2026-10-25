"""Bounded HTTP + RSS/Atom parsing shared by the zeroday source adapters.

Public endpoints only. Every helper returns ``None``/``[]`` on failure so one
dead upstream never aborts a sweep — the caller records the error.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from defusedxml.ElementTree import ParseError, fromstring

from rugbrain.zeroday.schema import parse_ts

log = logging.getLogger(__name__)

USER_AGENT = "RMI-RagBrain/0.1 (+https://rugmunch.io; zeroday ingestion)"
DEFAULT_TIMEOUT = 30.0
MAX_BYTES = 8_000_000


def proxy_url() -> str | None:
    """Optional proxy from ``PROXY_URL`` (fleet convention)."""
    return os.getenv("PROXY_URL", "").strip() or None


def make_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    """One pooled client per sweep, bounded follow-redirects and headers."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0),
        follow_redirects=True,
        max_redirects=5,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/xml, text/html;q=0.9, */*;q=0.5",
        },
        proxy=proxy_url(),
    )


def _bounded(response: httpx.Response) -> bool:
    length = response.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BYTES:
        log.warning("payload_too_large url=%s bytes=%s", response.url, length)
        return False
    return len(response.content) <= MAX_BYTES


async def get_text(client: httpx.AsyncClient, url: str) -> str | None:
    """GET a text body, or ``None`` on any HTTP/transport error."""
    try:
        response = await client.get(url)
        if response.status_code != httpx.codes.OK or not _bounded(response):
            log.warning("fetch_status url=%s code=%s", url, response.status_code)
            return None
        return str(response.text)
    except httpx.HTTPError as exc:
        log.warning("fetch_error url=%s err=%s", url, str(exc)[:160])
        return None


async def get_json(client: httpx.AsyncClient, url: str) -> Any | None:
    """GET a JSON body, or ``None`` on any HTTP/parse error."""
    try:
        response = await client.get(url)
        if response.status_code != httpx.codes.OK or not _bounded(response):
            log.warning("fetch_status url=%s code=%s", url, response.status_code)
            return None
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("fetch_error url=%s err=%s", url, str(exc)[:160])
        return None


async def post_json(client: httpx.AsyncClient, url: str, payload: dict[str, Any]) -> Any | None:
    """POST a JSON body, or ``None`` on any HTTP/parse error."""
    try:
        response = await client.post(url, json=payload)
        if response.status_code != httpx.codes.OK or not _bounded(response):
            log.warning("post_status url=%s code=%s", url, response.status_code)
            return None
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("post_error url=%s err=%s", url, str(exc)[:160])
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: Any, names: tuple[str, ...]) -> str:
    for child in node:
        if _local(child.tag) in names:
            return "".join(child.itertext()).strip()
    return ""


def parse_feed(text: str) -> list[dict[str, str]]:
    """Parse RSS 2.0 / Atom into ``title, link, published, summary`` dicts.

    Returns ``[]`` on malformed XML. ``defusedxml`` blocks entity-expansion
    attacks, which matters because these feeds are third-party content.
    """
    try:
        root = fromstring(text)
    except ParseError as exc:  # pragma: no cover - upstream corruption
        log.warning("feed_parse_error err=%s", str(exc)[:160])
        return []
    items: list[dict[str, str]] = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        link = _child_text(node, ("link",))
        if not link:
            for child in node:
                if _local(child.tag) == "link" and child.get("href"):
                    link = child.get("href", "")
                    break
        title = _child_text(node, ("title",))
        published = _child_text(node, ("pubdate", "published", "updated", "date"))
        summary = _child_text(node, ("description", "summary", "content"))
        if title and link:
            items.append(
                {
                    "title": title,
                    "link": link,
                    "published": published,
                    "summary": summary[:1500],
                }
            )
    return items


def within_days(value: Any, days: int, now: datetime | None = None) -> bool:
    """True when *value* parses and falls inside the last *days* window."""
    dt = parse_ts(value)
    if dt is None:
        return False
    reference = now or datetime.now(UTC)
    return dt >= reference - timedelta(days=days)


__all__ = [
    "DEFAULT_TIMEOUT",
    "MAX_BYTES",
    "USER_AGENT",
    "get_json",
    "get_text",
    "make_client",
    "parse_feed",
    "post_json",
    "proxy_url",
    "within_days",
]
