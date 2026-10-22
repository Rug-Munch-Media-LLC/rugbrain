"""Fetch an article body for a news row whose feed only carried a snippet.

Why this exists: the RSS path has never fetched article pages — every row's
"body" was ``description``. Roughly two thirds of the corpus is therefore a
feed snippet, which the slop gate correctly scores as a stub. This module
turns a publisher URL into prose, and refuses to pretend when it cannot:

* non-200 (403, 401, 402, 429) → ``blocked``, body stays the snippet;
* paywall / consent / bot-wall markers → ``blocked``;
* extracted prose shorter than ``MIN_ARTICLE_WORDS`` → ``blocked``.

Stdlib HTMLParser only (no readability dependency), one primary coroutine.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

import httpx

from rugbrain.rss_extract import MIN_ARTICLE_WORDS, clean_item

SKIP = frozenset(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"])
BLOCKED_MARKERS = (
    "enable javascript", "access denied", "just a moment", "are you a robot",
    "subscribe to continue", "this article is for subscribers", "consent",
    "403 forbidden", "unusual traffic", "verify you are human",
)
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class _Paragraphs(HTMLParser):
    """Collect text from <p>/<li> blocks, ignoring chrome containers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buf: list[str] = []
        self._skip = 0
        self._in_block = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP:
            self._skip += 1
        elif tag in ("p", "li", "blockquote"):
            self._in_block, self._buf = True, []

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP and self._skip:
            self._skip -= 1
        elif tag in ("p", "li", "blockquote") and self._in_block:
            text = " ".join("".join(self._buf).split())
            if len(text.split()) >= 6:
                self.blocks.append(text)
            self._in_block = False

    def handle_data(self, data: str) -> None:
        if self._in_block and not self._skip:
            self._buf.append(data)


def _prose(markup: str) -> str:
    parser = _Paragraphs()
    parser.feed(markup)
    return " ".join(parser.blocks)


async def fetch_article(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    """Return ``{'status', 'body', 'body_source'}`` for one publisher URL."""
    if not url.startswith("http") or "news.google.com" in url:
        return {"status": "unresolvable", "body": "", "body_source": "rss_summary"}
    try:
        resp = await client.get(url, headers={"User-Agent": UA},
                                timeout=httpx.Timeout(12.0, connect=6.0),
                                follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return {"status": f"error:{type(exc).__name__}", "body": "",
                "body_source": "rss_summary"}
    if resp.status_code != 200:
        return {"status": f"blocked:{resp.status_code}", "body": "", "body_source": "rss_summary"}
    low = resp.text[:20000].lower()
    if any(m in low for m in BLOCKED_MARKERS):
        return {"status": "blocked:wall", "body": "", "body_source": "rss_summary"}
    body = _prose(resp.text)
    if len(body.split()) < MIN_ARTICLE_WORDS:
        return {"status": "blocked:thin", "body": "", "body_source": "rss_summary"}
    return {"status": "ok", "body": body[:20000], "body_source": "article_fetch"}


def fallback(item: dict[str, Any]) -> dict[str, Any]:
    """Snippet kept as-is when the publisher refuses us (never invented)."""
    return clean_item(item)
