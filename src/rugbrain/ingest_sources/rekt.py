"""Rekt news source — post-mortem articles of major DeFi hacks.

Source: https://rekt.news — post-mortem writeups of the largest DeFi
exploits, each ranked by USD value lost.

Status notes (verified 2026-08-16):

  * The legacy RSS feed (``https://rekt.news/feed/`` — also ``/feed.xml``
    and ``/rss``) returns HTTP 500 — dead. ``historical.scrape_rekt_news``
    and any RSS-based consumer must NOT rely on it.
  * The leaderboard page (``https://rekt.news/leaderboard``) DOES still
    embed the ``__NEXT_DATA__`` JSON blob (314 entries as of 2026-08-16)
    with slug/title/date/amount/tags per incident. This source parses that
    blob (``parse_leaderboard_next_data``), with a legacy fallback to
    server-rendered ``/slug-rekt/`` anchor links for older fixtures.
  * The maintained structured replacement for *fresh* incident data remains
    the DeFiLlama hacks API (see ``ingest_sources.defillama``); Rekt here
    is the historical post-mortem index.

doc_type: ``post_mortem``. Point IDs are slug-based (``rekt_news:{slug}``)
so re-ingestion is idempotent — the legacy rank suffix was unstable (ranks
shift when new entries are added).
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

LEADERBOARD_URL = "https://rekt.news/leaderboard"
DEFAULT_COLLECTION = "defi_exploits"
DOC_TYPE = "post_mortem"

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_ANCHOR_RE = re.compile(r'<a[^>]+href="(/[^"]*-rekt/)"[^>]*>(.*?)</a>', re.DOTALL)
_TEXT_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class RektArticle:
    """An entry in the Rekt leaderboard index."""

    rank: int
    title: str
    url: str
    slug: str = ""
    date: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


def parse_leaderboard_next_data(html_text: str) -> list[RektArticle]:
    """Extract leaderboard entries from the ``__NEXT_DATA__`` JSON blob.

    rekt.news renders the leaderboard client-side; the page embeds the
    entry list in ``pageProps.leaderboard`` (slug, title, rekt.amount,
    rekt.date, tags, excerpt). Returns ``[]`` when the blob is absent or
    the payload shape changes (never raises).
    """
    m = _NEXT_DATA_RE.search(html_text or "")
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError as e:
        log.debug("rekt_next_data_parse_failed err=%s", str(e)[:120])
        return []
    entries = data.get("props", {}).get("pageProps", {}).get("leaderboard", []) or []
    articles: list[RektArticle] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("slug") or "").strip().strip("/")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        title = str(entry.get("title") or "").strip() or slug
        rekt = entry.get("rekt") or {}
        date = str(rekt.get("date") or entry.get("date") or "").strip()
        tags = tuple(str(t) for t in (entry.get("tags") or []) if str(t).strip())
        articles.append(
            RektArticle(
                rank=len(articles) + 1,
                title=title,
                url=f"/{slug}/",
                slug=slug,
                date=date,
                tags=tags,
            )
        )
    return articles


def parse_leaderboard(html_text: str) -> list[RektArticle]:
    """Extract leaderboard entries from *html_text*.

    Prefers the ``__NEXT_DATA__`` JSON blob (current rekt.news rendering);
    falls back to server-rendered ``/slug-rekt/`` anchor links (older
    pages / test fixtures). Pure function (no network) so it can be
    unit-tested. Entries are deduplicated by slug and returned in page
    order.
    """
    articles = parse_leaderboard_next_data(html_text)
    if articles:
        return articles
    # Legacy fallback: each leaderboard row links to the article.
    articles = []
    seen: set[str] = set()
    for m in _ANCHOR_RE.finditer(html_text or ""):
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
    """Normalize a leaderboard entry into a post_mortem IngestDoc.

    Deterministic idempotent ``doc_id`` (``rekt_news:{slug}``) — the store
    layer converts it to a UUIDv5 point ID, so re-ingestion overwrites the
    same points. (The legacy rank suffix was dropped: leaderboard ranks
    shift as new entries are added.)
    """
    url = f"https://rekt.news{article.url}"
    slug = article.slug or article.url.strip("/")
    lines = [
        f"Rekt post-mortem — {article.title}.",
        f"Rank: #{article.rank} on the Rekt leaderboard (by value lost).",
        f"Read the full post-mortem at {url}.",
    ]
    metadata: dict[str, object] = {
        "collection": DEFAULT_COLLECTION,
        "rank": article.rank,
        "slug": slug,
        "source_url": url,
        "doc_type": DOC_TYPE,
    }
    if article.date:
        lines.append(f"Hack date: {article.date}")
        metadata["date"] = article.date
    if article.tags:
        lines.append(f"Tags: {', '.join(article.tags)}")
        metadata["tags"] = list(article.tags)
    return IngestDoc(
        content="\n".join(lines) + "\n",
        doc_id=f"rekt_news:{slug}",
        doc_type=DOC_TYPE,
        source="rekt_news",
        title=article.title,
        url=url,
        metadata=metadata,
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
    "parse_leaderboard_next_data",
]
