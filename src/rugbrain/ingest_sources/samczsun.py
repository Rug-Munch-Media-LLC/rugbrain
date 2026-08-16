"""samczsun.com — canonical security-research corpus (research_v2).

Source: https://samczsun.com/ — samczsun's security research writeups plus
the full Findings archive (2017-2026). No RSS/sitemap is published (404
verified), so the source crawls the article index on the homepage and
fetches individual post pages.

Post pages are server-rendered HTML with the article inside an ``<article>``
element (verified live 2026-08-16: 200 with a browser User-Agent; the site
307-redirects bare curl). Content is extracted by stripping tags from the
article element — a tolerant, dependency-free extractor.

Routes to ``research_v2`` (the on-demand research collection), doc_type
``research``.

NOTE [REVIEW]: article discovery is homepage-link-based and extraction is
regex-based on the current Next.js markup — the site layout may change.
Fetches are bounded by ``limit`` (default cap 30 posts) and fail open on
any per-post error.

Design rules (consistent with ``base.py``):

  * Fail-open: a blocked post or a changed homepage logs + continues.
  * Deterministic ``doc_id`` (``samczsun_blog:<slug>``) — slugs are stable
    URLs → idempotent re-ingestion.
  * Credentials: none.
"""

from __future__ import annotations

import logging
import re

import httpx

from rugbrain.ingest_sources.base import IngestDoc, Source

log = logging.getLogger(__name__)

DEFAULT_COLLECTION = "research_v2"
DEFAULT_CAP = 30  # bounded crawl; article text is long
HOME_URL = "https://samczsun.com/"
SOURCE_URL = "https://samczsun.com/"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)

# Article slugs look like /escaping-the-dark-forest ; exclude anchors, assets,
# framework chunks and single-char/known chrome paths.
_SLUG_RE = re.compile(r'href="/([a-z0-9][a-z0-9-]{2,})/"', re.I)
_EXCLUDED = {
    "about",
    "contact",
    "findings",
    "press",
    "talks",
    "writing",
    "icon",
    "favicon",
    "feed",
    "rss",
    "sitemap",
}
_ARTICLE_RE = re.compile(r"<article[^>]*>(.*?)</article>", re.S | re.I)
_SCRIPT_RE = re.compile(r"<script.*?</script>", re.S | re.I)
_STYLE_RE = re.compile(r"<style.*?</style>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def extract_article_links(home_html: str) -> list[str]:
    """Extract article slug links from the homepage, de-duplicated, ordered."""
    links: list[str] = []
    for m in _SLUG_RE.finditer(home_html or ""):
        slug = m.group(1).lower()
        if slug in _EXCLUDED:
            continue
        if slug not in links:
            links.append(slug)
    return links


def extract_article_text(post_html: str) -> tuple[str, str]:
    """Extract ``(title, article_text)`` from a post page; tolerant.

    Returns the raw tag-stripped article text (title from the ``<title>``
    tag, falling back to ``samczsun post``).
    """
    html = post_html or ""
    m = _TITLE_RE.search(html)
    title = m.group(1).strip() if m else ""
    title = re.sub(r"\s*-\s*samczsun\s*$", "", title, flags=re.I).strip()
    m = _ARTICLE_RE.search(html)
    body = m.group(1) if m else ""
    body = _SCRIPT_RE.sub(" ", body)
    body = _STYLE_RE.sub(" ", body)
    body = _TAG_RE.sub("\n", body)
    body = _WS_RE.sub(" ", body)
    body = _MULTI_NL_RE.sub("\n\n", body).strip()
    return title or "samczsun post", body


def article_to_doc(slug: str, title: str, body: str) -> IngestDoc:
    """Normalize one article into an IngestDoc."""
    content = f"samczsun research — {title}.\n\n{body[:12000]}"
    url = f"{SOURCE_URL}{slug}/"
    return IngestDoc(
        content=content,
        doc_id=f"samczsun_blog:{_slug(slug)}",
        metadata={
            "collection": DEFAULT_COLLECTION,
            "author": "samczsun",
            "severity": "medium",
            "ecosystem": "EVM",
            "source_url": url,
            "doc_type": "research",
        },
        doc_type="research",
        source="samczsun_blog",
        title=title,
        url=url,
    )


class SamczsunSource(Source):
    """samczsun.com — security research writeups + Findings archive.

    Crawls the homepage article index, fetches post pages (bounded by
    ``limit``), and emits one ``IngestDoc`` per post into ``research_v2``
    (doc_type ``research``).
    """

    name = "samczsun_blog"
    doc_type = "research"
    default_collection = DEFAULT_COLLECTION
    description = "samczsun security research writeups + findings archive (research_v2)"
    timeout = 60.0

    async def _fetch(self, client: httpx.AsyncClient, limit: int | None) -> list[IngestDoc]:
        cap = DEFAULT_CAP if limit is None else min(limit, DEFAULT_CAP)
        headers = {"User-Agent": BROWSER_UA}
        try:
            r = await client.get(HOME_URL, headers=headers, timeout=self.timeout)
            home_html = r.text if r.status_code == 200 else ""
        except Exception as e:
            log.warning("samczsun_home_error err=%s", str(e)[:160])
            home_html = ""
        slugs = extract_article_links(home_html)
        if not slugs:
            log.warning("samczsun_home_empty (no article links found)")
            return []

        docs: list[IngestDoc] = []
        for slug in slugs[:cap]:
            url = f"{SOURCE_URL}{slug}/"
            try:
                r = await client.get(url, headers=headers, timeout=self.timeout)
                if r.status_code != 200:
                    log.debug("samczsun_post_status slug=%s status=%d", slug, r.status_code)
                    continue
                title, body = extract_article_text(r.text)
            except Exception as e:
                log.warning("samczsun_post_error slug=%s err=%s", slug, str(e)[:160])
                continue
            if body:
                docs.append(article_to_doc(slug, title, body))
            if len(docs) >= cap:
                break
        if not docs:
            log.warning("samczsun_fetch_empty")
        else:
            log.info("samczsun_feed_ok posts=%d", len(docs))
        return docs


__all__ = [
    "DEFAULT_COLLECTION",
    "SamczsunSource",
    "article_to_doc",
    "extract_article_links",
    "extract_article_text",
]
