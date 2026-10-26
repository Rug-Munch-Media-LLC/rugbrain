"""Quality gates for returned feed/search items.

Two jobs, both about not shipping noise as if it were content:

1. :func:`is_publishable_news` rejects scraped page chrome. The news collections
   hold raw crawls as well as articles, so a generic query returns nav menus,
   cookie-consent CSS and inline JSON-LD as "news". A caller cannot tell those
   from an article, so the gate runs before anything is returned.
2. :func:`dedupe_by_content` collapses the same story reached through several
   collections. A multi-collection brief that concatenates per-collection
   results repeats itself, and repetition reads as corroboration.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

#: Marker substrings of page chrome, not editorial text.
_CHROME_MARKERS: tuple[str, ...] = (
    "visibility: hidden",
    "w-mod-js",
    "document.documentElement",
    "@context",
    "__NEXT_DATA__",
    "cookie-consent",
    "cookieconsent",
    "window.dataLayer",
    "<html",
    "<script",
    "<style",
)

#: A real article is longer than a nav label. Measured on the live collections:
#: chrome fragments run 40-400 chars, articles 400+.
_MIN_CONTENT_CHARS = 200


def is_publishable_news(item: dict[str, Any]) -> bool:
    """True when *item* reads as editorial content rather than page chrome."""
    content = str(item.get("content", "") or "").strip()
    if len(content) < _MIN_CONTENT_CHARS:
        return False
    lowered = content.lower()
    if any(marker.lower() in lowered for marker in _CHROME_MARKERS):
        return False
    alnum = sum(ch.isalnum() or ch.isspace() for ch in lowered)
    return alnum / len(lowered) >= 0.6


def _content_key(item: dict[str, Any]) -> str:
    """Normalized identity of the story, ignoring source and score noise."""
    content = re.sub(r"\s+", " ", str(item.get("content", "") or "")).strip().lower()
    if not content:
        return ""
    return hashlib.sha256(content[:400].encode()).hexdigest()


def dedupe_by_content(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop repeats, keeping the first (already score-sorted) occurrence."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = _content_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


__all__ = ["dedupe_by_content", "is_publishable_news"]
