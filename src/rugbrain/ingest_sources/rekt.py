"""Rekt news source — post-mortem articles of major DeFi hacks.

Source: https://rekt.news — post-mortem writeups of the largest DeFi
exploits, each ranked by USD value lost.

Status notes:

  * The legacy RSS feed (``https://rekt.news/feed/``) returns HTTP 500 and
    the leaderboard page no longer embeds ``__NEXT_DATA__`` JSON — both are
    dead. The maintained structured replacement for incident data is the
    DeFiLlama hacks API (see ``ingest_sources.defillama``).
  * This source therefore scaffolds a lightweight leaderboard scrape:
    ``fetch()`` GETs ``https://rekt.news/leaderboard`` once and extracts the
    article index (slug/title/rank). Full per-article text extraction is a
    follow-up (the article HTML contains the post body; a minimal
    HTML→text pass is provided but kept conservative).

doc_type: ``post_mortem``.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

LEADERBOARD_URL = "https://rekt.news/leaderboard"
DEFAULT_COLLECTION = "defi_exploits"
DOC_TYPE = "post_mortem"

_SLUG_RE = re.compile(r'href="(/[a-z0-9-]+-rekt/)"', re.I)
_TEXT_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class RektArticle:
    """An entry in the Rekt leaderboard index."""

    rank: int
    title: str
    url: str


def parse_leaderboard(html_text: str) -> list[RektArticle]:
    """Extract ``/slug-rekt/`` article links + anchor text from the page.

    Pure function (no network) so it can be unit-tested with a fixture.
    Entries are deduplicated by URL and returned in page order.
    """
    articles: list[RektArticle] = []
    seen: set[str] = set()
    # Each leaderboard row links to the article; the anchor text is the title.
    for m in re.finditer(
        r'<a[^>]+href="(/[^"]*-rekt/)"[^>]*>(.*?)</a>', html_text or "", re.DOTALL
    ):
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        title = html.unescape(_TEXT_RE.sub("", m.group(2))).strip()
        if not title:
            title = slug.strip("/")
        articles.append(RektArticle(rank=len(articles) + 1, title=title, url=slug))
    return articles


def article_to_doc(article: RektArticle) -> IngestDoc:
    """Normalize a leaderboard entry into a post_mortem IngestDoc."""
    url = f"https://rekt.news{article.url}"
    content = (
        f"Rekt post-mortem — {article.title}.\n"
        f"Rank: #{article.rank} on the Rekt leaderboard (by value lost).\n"
        f"Read the full post-mortem at {url}.\n"
    )
    return IngestDoc(
        content=content,
        doc_id=f"rekt_news:{article.url.strip('/')}:{article.rank}",
        doc_type=DOC_TYPE,
        source="rekt_news",
        title=article.title,
        url=url,
        metadata={
            "collection": DEFAULT_COLLECTION,
            "rank": article.rank,
            "source_url": url,
            "doc_type": DOC_TYPE,
        },
    )


class RektSource(Source):
    """Rekt.news leaderboard index (post-mortem article list)."""

    name = "rekt_news"
    doc_type = DOC_TYPE
    default_collection = DEFAULT_COLLECTION
    description = "Rekt.news post-mortems of major DeFi hacks (leaderboard index)"
    timeout = 30.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        try:
            r = await client.get(LEADERBOARD_URL)
        except Exception as e:
            log.warning("rekt_fetch_failed err=%s", str(e)[:200])
            return []
        if r.status_code != 200:
            log.warning("rekt_fetch_status status=%d", r.status_code)
            return []
        articles = parse_leaderboard(r.text)
        if limit:
            articles = articles[:limit]
        return [article_to_doc(a) for a in articles]


__all__ = [
    "DEFAULT_COLLECTION",
    "DOC_TYPE",
    "LEADERBOARD_URL",
    "RektArticle",
    "RektSource",
    "article_to_doc",
    "parse_leaderboard",
]
