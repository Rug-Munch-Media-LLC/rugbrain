"""Sitemap half of the security web intake: find article URLs, read one article.

Publisher feeds die (BlockSec retired ``/feed``) while the site keeps publishing
and keeps a sitemap. This module turns ``sitemap.xml`` into a bounded list of
newest article URLs and turns one article page into ``(title, summary, prose)``
— no new dependency, no JS renderer, no scraping service.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlsplit

#: article URL shapes on a publisher sitemap (locale prefixes excluded below).
ARTICLE_RE = re.compile(r"^/blog/[a-z0-9][a-z0-9-]{6,}/?$", re.I)
#: localized paths look like /ko-kr/blog/<slug> — only the bare /blog/ path is english.
LOCALE_RE = re.compile(r"^/[a-z]{2}(-[a-z]{2,4})?/", re.I)
SKIP_RE = re.compile(r"(/tag/|/category/|/author/|/page/|/about|/contact|/privacy)", re.I)
LASTMOD_RE = re.compile(r"<(?:lastmod|news:publication_date)>([^<]+)<")


async def sitemap_articles(client: Any, feed_url: str, cap: int) -> list[tuple[str, str]]:
    """``[(url, lastmod)]`` — article-shaped, one locale, newest lastmod first."""
    try:
        resp = await client.get(feed_url)
    except Exception:  # a dead sitemap is data, not a crash
        return []
    if resp.status_code != 200:
        return []
    out: list[tuple[str, str]] = []
    for block in re.findall(r"<url>(.*?)</url>", resp.text, re.S):
        loc = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", block)
        if not loc:
            continue
        url = html.unescape(loc.group(1))
        path = urlsplit(url).path
        if not ARTICLE_RE.search(path) or LOCALE_RE.search(path) or SKIP_RE.search(path):
            continue
        stamp = LASTMOD_RE.search(block)
        out.append((url, stamp.group(1) if stamp else ""))
    out.sort(key=lambda t: t[1], reverse=True)
    return out[:cap]


def article_text(body: str) -> tuple[str, str, str]:
    """``(title, summary, prose)`` from an article HTML page."""
    title = ""
    for pat in (
        r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)',
        r"<title[^>]*>(.*?)</title>",
    ):
        m = re.search(pat, body, re.S | re.I)
        if m:
            title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
            break
    m = re.search(
        r'(?:name|property)=["\'](?:description|og:description)["\'][^>]*content=["\']([^"\']+)',
        body,
        re.I,
    )
    summary = html.unescape(m.group(1))[:2000] if m else ""
    prose = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    prose = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", prose))).strip()
    return title[:500], summary, prose[:24000]


__all__ = ["article_text", "sitemap_articles"]
