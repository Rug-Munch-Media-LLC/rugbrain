"""Nitter RSS parsing helpers (tiny, dependency-free)."""

from __future__ import annotations

import re

from .dates import _parse_date_iso


def _tag_text(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S)
    if not m:
        return ""
    return re.sub(r"<[^>]+>", "", m.group(1)).strip()


def _parse_rss_items(xml: str, account: str) -> list[tuple[str, str, str, str]]:
    """Very small RSS parser — enough for Nitter's item/title/link/pubDate."""
    items: list[tuple[str, str, str, str]] = []
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        block = m.group(1)
        title = _tag_text(block, "title")
        link = _tag_text(block, "link")
        pub_date = _parse_date_iso(_tag_text(block, "pubDate"))
        summary = _tag_text(block, "description")
        if title:
            items.append((title, link, pub_date, summary))
    return items
