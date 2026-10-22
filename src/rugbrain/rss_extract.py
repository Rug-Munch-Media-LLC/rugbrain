"""Body extraction for RSS items: HTML → prose, boilerplate stripped, honest flags.

The defect this closes: ``rss_store`` stored ``description`` verbatim in
``summary`` and hard-coded ``body_markdown = NULL``.  For Google News the
description is a truncated ``<a href="https://news.google.com/rss/articles/...">``
fragment, and for Yahoo the scraped page opens with navigation chrome
("Oops, something went wrong Skip to navigation ...").  Either way the stored
"body" was never an article.

Rules:
* never invent prose — a blocked/absent article keeps the snippet;
* every row states where its body came from (``body_source``);
* a row whose body is a feed snippet, not an article, is ``truncated``.

Dependency-free (stdlib only); the ingest venv has no bs4.
"""

from __future__ import annotations

import html
import re
from typing import Any

# Page chrome seen in scraped/boilerplate-led bodies. Removed in order.
BOILERPLATE = (
    r"oops, something went wrong",
    r"skip to navigation",
    r"skip to main content",
    r"skip to right column",
    r"skip to left column",
    r"skip to content",
    r"u\.s\. markets open in \d+h(?:\s*\d+m)?",
    r"u\.s\. markets closed",
    r"advertisement",
    r"sign in to continue",
    r"this content is not available due to your privacy preferences",
    r"enable javascript to continue",
)
TICKER_STRIP = re.compile(
    r"(?:[A-Z0-9 .&'-]{2,40} [+-][\d.,]+ [+-][\d.,]+%){3,}", re.IGNORECASE)
TAGS = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")
GOOGLE_NEWS = re.compile(r"https?://news\.google\.com/(?:rss/)?articles/([A-Za-z0-9_\-]+)")
SNIPPET_CHARS = 240  # a feed snippet, not an article
MIN_ARTICLE_WORDS = 40


def _plain(text: str) -> str:
    """Tags out, entities decoded, whitespace collapsed."""
    return SPACE.sub(" ", html.unescape(TAGS.sub(" ", text or ""))).strip()


def _strip_boilerplate(text: str) -> str:
    lowered = text.lower()
    for phrase in BOILERPLATE:
        if phrase not in lowered:
            continue
        # Drop the chrome run: everything up to the last chrome marker in the
        # opening 600 chars, which is where nav blocks live.
        head = lowered[:600]
        at = head.rfind(phrase)
        if at != -1:
            text = text[at + len(phrase):].lstrip(" .-–|")
            lowered = text.lower()
    text = TICKER_STRIP.sub(" ", text)
    return SPACE.sub(" ", text).strip()


def _looks_like_chrome(text: str) -> bool:
    """Mostly navigation: many short fragments, almost no sentence punctuation."""
    words = text.split()
    if len(words) < MIN_ARTICLE_WORDS:
        return True
    sentences = [s for s in re.split(r"[.!?]", text) if len(s.split()) >= 6]
    return len(sentences) < 2


def clean_item(item: dict[str, Any], source: str = "") -> dict[str, Any]:
    """Return ``body``, ``body_source`` and ``truncated`` for one feed item.

    ``body_source`` values: ``article_html`` (usable prose), ``rss_summary``
    (feed description was all we had), ``google_news_snippet`` (aggregator
    redirect we cannot resolve without executing Google's JS).
    """
    desc_raw = item.get("description") or ""
    desc = _plain(desc_raw)
    link = (item.get("link") or item.get("guid") or "").strip()
    body = _strip_boilerplate(desc)

    if GOOGLE_NEWS.search(link) or "news.google.com/rss/articles" in desc_raw:
        # The redirect resolves only through Google's JS/batchexecute shell
        # (HTTP 400 without it) — store the snippet, never a fake body.
        return {"body": body or _plain(item.get("title") or ""),
                "body_source": "google_news_snippet", "truncated": True}

    words = len(body.split())
    if words >= MIN_ARTICLE_WORDS and not _looks_like_chrome(body):
        return {"body": body, "body_source": "article_html", "truncated": False}

    return {"body": body or _plain(item.get("title") or ""),
            "body_source": "rss_summary", "truncated": True}
