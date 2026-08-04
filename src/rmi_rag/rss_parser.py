"""Minimal RSS 2.0 / Atom feed parser.

Deliberately regex-based (no XML parser): crypto feeds are untrusted input
and Python's stdlib ElementTree has a history of entity-expansion surprises.
This keeps the parsing surface tiny and side-effect free — the same
philosophy as the Nitter RSS parser in ``rmi_rag.alerts``.

Supported shapes:
  * RSS 2.0 ``<item>`` blocks: ``title``, ``link``, ``pubDate``, ``description``
  * Atom ``<entry>`` blocks: ``title``, ``link href``, ``published``, ``content``/``summary``

``parse_feed`` returns ``(feed_title, items)`` where each item is a dict with
``title``, ``url``, ``published`` (ISO-8601-ish string), and ``summary``.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

__all__ = ["parse_feed", "parse_rss"]

_ITEM_RE = re.compile(r"<(item|entry)\b[^>]*>(.*?)</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<([A-Za-z0-9:_-]+)\b[^>]*>(.*?)</\1>", re.S | re.I)
_ATTR_RE = re.compile(r"<([A-Za-z0-9:_-]+)\b([^>]*)>", re.S | re.I)
_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.I)
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
_STRIP_TAGS_RE = re.compile(r"<[^>]+>")

# RFC 822 / RFC 3339 date patterns commonly emitted by crypto feeds.
_DATE_PATTERNS = (
    "%a, %d %b %Y %H:%M:%S %z",  # RSS pubDate
    "%a, %d %b %Y %H:%M:%S %Z",
    "%Y-%m-%dT%H:%M:%S%z",  # Atom published
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def _decode(value: str) -> str:
    """Strip CDATA wrappers, HTML tags, and unescape entities."""
    value = _CDATA_RE.sub(lambda m: m.group(1), value or "")
    value = _STRIP_TAGS_RE.sub("", value)
    return html.unescape(value).strip()


def _tag_text(block: str, tag: str) -> str:
    """Return decoded text content of the first ``<tag>`` inside *block*."""
    for m in _TAG_RE.finditer(block):
        if m.group(1).lower() == tag.lower():
            return _decode(m.group(2))
    return ""


def _link_from_entry(block: str) -> str:
    """Extract ``<link href=...>`` from an Atom entry, then plain ``<link>``."""
    for m in _ATTR_RE.finditer(block):
        if m.group(1).lower() != "link":
            continue
        href = _HREF_RE.search(m.group(2) or "")
        if href:
            return html.unescape(href.group(1)).strip()
    return _decode(_tag_text(block, "link"))


def _parse_date(raw: str) -> str:
    """Best-effort parse of common RSS/Atom dates to an ISO string."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    return raw


def parse_rss(xml: str, limit: int = 50) -> list[dict[str, Any]]:
    """Parse an RSS 2.0 or Atom XML document into item dicts.

    Each item: ``{"title", "url", "published", "summary", "is_atom"}``.
    """
    items: list[dict[str, Any]] = []
    for m in _ITEM_RE.finditer(xml or ""):
        tag, block = m.group(1).lower(), m.group(2)
        is_atom = tag == "entry"
        title = _tag_text(block, "title")
        link = _link_from_entry(block) if is_atom else _tag_text(block, "link")
        published = _parse_date(_tag_text(block, "published") or _tag_text(block, "pubDate"))
        if is_atom:
            summary = _tag_text(block, "content") or _tag_text(block, "summary")
        else:
            summary = _tag_text(block, "description")
        if not title and not summary:
            continue
        items.append(
            {
                "title": title,
                "url": link,
                "published": published,
                "summary": summary[:5000],
                "is_atom": is_atom,
            }
        )
        if len(items) >= limit:
            break
    return items


def parse_feed(xml: str, limit: int = 50) -> tuple[str, list[dict[str, Any]]]:
    """Parse a feed and return ``(feed_title, items)``."""
    feed_title = ""
    m = re.search(r"<(title)\b[^>]*>(.*?)</\1>", xml or "", re.S | re.I)
    if m:
        feed_title = _decode(m.group(2))[:200]
    return feed_title, parse_rss(xml, limit=limit)
