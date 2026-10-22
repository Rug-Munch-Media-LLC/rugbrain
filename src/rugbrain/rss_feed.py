"""Feed fetching primitives shared by the RSS probe and the ingest.

Parsing is delegated to :mod:`rugbrain.rss_parser` (the regex parser the crawler
and realtime alerts already use) — one parsing surface for untrusted XML, no
second implementation. What this module adds is the transport half:

* a **conditional GET** — replay ``ETag`` / ``Last-Modified`` and treat 304 as
  "nothing changed" instead of re-parsing an unchanged body;
* a **total timeout** per feed, so one slow host cannot stall a batch;
* a shape that never raises: a dead feed is data, not an exception.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from rugbrain.rss_parser import parse_rss

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) RugMunch-RAG/2.0 (admin@rugmunch.io)"
ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"
ENTRIES_PER_FEED = 50


def read_feeds_file(path: str | Path) -> list[tuple[str, str]]:
    """Parse ``URL [Source Name]`` lines (blank lines and ``#`` comments ignored)."""
    feeds: list[tuple[str, str]] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        url = parts[0]
        name = parts[1].strip() if len(parts) > 1 else re.sub(r"^https?://", "", url).split("/")[0]
        feeds.append((url, name))
    return feeds


def parse_entries(body: str) -> tuple[list[dict[str, Any]], str]:
    """Return (entries, fmt) with the ingest's flat item shape."""
    items = parse_rss(body, limit=ENTRIES_PER_FEED)
    fmt = "atom" if items and items[0].get("is_atom") else ("rss" if items else "unknown")
    return (
        [
            {
                "title": it["title"],
                "link": it["url"],
                "guid": "",
                "description": it["summary"],
                "date": it["published"],
            }
            for it in items
        ],
        fmt,
    )


def parse_ts(value: str) -> float | None:
    """RFC-822 or ISO-8601 → epoch seconds; None when unparseable."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).timestamp()
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


async def fetch_feed(
    client: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    """Conditional GET + parse. Always returns a record; never raises."""
    rec: dict[str, Any] = {
        "url": url,
        "http_status": 0,
        "items": [],
        "fmt": "unknown",
        "not_modified": False,
        "etag": None,
        "last_modified": None,
        "error": None,
    }
    try:
        resp = await client.get(
            url, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT, **(headers or {})}
        )
    except (httpx.HTTPError, OSError) as exc:
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return rec
    rec["http_status"] = resp.status_code
    rec["etag"] = resp.headers.get("etag")
    rec["last_modified"] = resp.headers.get("last-modified")
    if resp.status_code == 304:
        rec["not_modified"] = True
        return rec
    if resp.status_code != 200:
        rec["error"] = f"HTTP {resp.status_code}"
        return rec
    body = resp.text
    if body.lstrip()[:200].lower().startswith(("{", "[")):
        rec["fmt"] = "json"
        rec["error"] = "json feed (unsupported)"
        return rec
    entries, fmt = parse_entries(body)
    rec["fmt"], rec["items"] = fmt, entries
    if not entries:
        rec["error"] = "no entries parsed"
    return rec


def newest_epoch(entries: list[dict[str, Any]]) -> float | None:
    stamps = [t for t in (parse_ts(e.get("date", "")) for e in entries) if t]
    return max(stamps) if stamps else None


def utcnow() -> datetime:
    return datetime.now(UTC)
