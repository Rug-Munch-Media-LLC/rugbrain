"""Pure helper functions for the RAG crawler (extracted from ``app/rag/crawler.py``).

Degodding split (2026-08-13): the article-level helpers (domain extraction,
spam/clickbait gates, HTML boilerplate stripping, summarisation, title
cleaning, language detection, Crawl4AI result unwrapping, source loading,
quality / relevance / freshness scoring) live here so ``crawler.py`` stays
focused on the crawl lifecycle (discovery → quality gates → ingest).

``crawler.py`` re-exports every public name so ``crawler.<name>`` references
in tests and callers keep resolving. No ClickHouse / Redis access here —
everything is pure over article dicts / HTML strings.
"""

from __future__ import annotations

import email.utils
import hashlib
import html
import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from rugbrain._logging import get_logger
from rugbrain.ingest._crawler_constants import (
    _AGENCY_RE,
    _BLOCK_TAGS,
    _BOILERPLATE_FRAGMENTS,
    _BOILERPLATE_TAGS,
    _CONTENT_FRAGMENTS,
    _CRAWLABLE_TYPES,
    _DEFI_RE,
    _ENGLISH_ASCII_THRESHOLD,
    _ENGLISH_MIN_WORDS,
    _LEGACY_SOURCES_FILE,
    _PAYWALL_MARKERS,
    _PROTOCOL_RE,
    _REGULATORY_RE,
    _SECURITY_RE,
    _SENTENCE_BOUNDARY_RE,
    _SOURCES_FILE,
    _TAG_RE,
    _TOKEN_RE,
    FRESH_AGE_HOURS,
    LOW_QUALITY_DOMAINS,
    MAX_ARTICLE_AGE_HOURS,
    SPAM_INDICATORS,
)

log = get_logger(__name__)


def _article_domain(url: str) -> str:
    """Bare lowercase hostname for an article URL (``''`` when unparseable)."""
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def is_spam_or_clickbait(title: str, domain: str) -> bool:
    """Filter out spam and clickbait articles."""
    if domain in LOW_QUALITY_DOMAINS:
        return True
    title_lower = title.lower()
    return any(indicator in title_lower for indicator in SPAM_INDICATORS)


def filter_spam(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only articles that pass the spam/clickbait gate."""
    kept: list[dict[str, Any]] = []
    for item in items:
        domain = _article_domain(str(item.get("url") or ""))
        if is_spam_or_clickbait(str(item.get("title") or ""), domain):
            log.info("article_spam_filtered", url=item.get("url"), domain=domain)
            continue
        kept.append(item)
    return kept


def _find_content_end(html_text: str, open_tag: str, start: int) -> int:
    """Index just past the matching close tag for *open_tag* at *start*.

    Handles self-closing tags and nested same-tag elements. Returns
    ``len(html_text)`` when the tree is unbalanced (caller falls back to
    the whole document).
    """
    depth = 0
    for match in _TAG_RE.finditer(html_text, start):
        closing = match.group(1) or ""
        tag = match.group(2) or ""
        self_close = match.group(4) or ""
        if tag.lower() != open_tag.lower():
            continue
        if self_close:
            continue
        if closing:
            depth -= 1
            if depth == 0:
                return match.end()
        else:
            depth += 1
    return len(html_text)


def _extract_content_region(html_text: str) -> str:
    """Isolate the primary article body container.

    Prefers semantic containers (``<article>`` then ``<main>``), then
    common content-div classes (``article-body``, ``post-content``, ...).
    Returns the whole document when no content container is found — the
    boilerplate stripper then drops sidebar/footer/cookie noise.
    """
    lower = html_text.lower()
    for tag in ("article", "main"):
        match = re.search(rf"<{tag}(?=[\s>])", lower)
        if match:
            start = match.start()
            return html_text[start : _find_content_end(html_text, tag, start)]
    for match in _TAG_RE.finditer(html_text):
        closing = match.group(1) or ""
        tag = match.group(2) or ""
        self_close = match.group(4) or ""
        if closing or self_close:
            continue
        if tag.lower() not in ("div", "section"):
            continue
        attrs = match.group(3) or ""
        if any(frag in attrs.lower() for frag in _CONTENT_FRAGMENTS):
            return html_text[match.start() : _find_content_end(html_text, tag, match.start())]
    return html_text


class _BoilerplateStripper(HTMLParser):
    """HTMLParser that drops boilerplate subtrees and emits article text.

    Uses ``convert_charrefs=True`` so HTML entities are unescaped on the
    way out. Block-level tags become paragraph breaks; inline tags are
    transparent.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            self._skip_depth += 1
            return
        attr_text = " ".join(f"{k}={v}" for k, v in attrs if v is not None)
        if tag in _BOILERPLATE_TAGS or any(frag in attr_text.lower() for frag in _BOILERPLATE_FRAGMENTS):
            self._skip_depth = 1
            return

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth or tag in _BOILERPLATE_TAGS:
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        """Visible article text with block boundaries as newlines."""
        return "".join(self._parts)


def _normalise_whitespace(text: str) -> str:
    """Collapse horizontal runs, drop blank lines, cap consecutive newlines.

    Preserves paragraph breaks (double newline) so downstream scoring
    (``score_article_quality``) and summarisation still see structure.
    """
    if not text:
        return ""
    lines = [re.sub(r"[ \t\f\v]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n\n".join(lines).strip()


def clean_article_html(html_text: str) -> str:
    """Strip boilerplate and extract clean article text from raw HTML.

    Pipeline:
      1. Isolate the primary content container (``<article>``/``<main>``
         or a content-div class) when the document has one.
      2. Drop boilerplate subtrees (scripts, styles, nav, cookie banners,
         newsletter signups, related-article rails, sidebars, popups).
      3. Unescape entities and normalise whitespace, keeping paragraph
         breaks.

    Paywalled articles return whatever text is visible in the DOM — the
    downstream quality gate (``score_article_quality``) rejects locked
    teasers via ``_looks_paywalled``.
    """
    if not html_text or not html_text.strip():
        return ""
    region = _extract_content_region(html_text)
    parser = _BoilerplateStripper()
    try:
        parser.feed(region)
        parser.close()
    except Exception:  # pragma: no cover - malformed HTML must never kill a crawl
        # Last-resort tag strip: no paragraph structure, but text survives.
        return _normalise_whitespace(re.sub(r"<[^>]+>", " ", region))
    return _normalise_whitespace(parser.text())


def generate_summary(text: str, max_chars: int = 300) -> str:
    """Extract first 2-3 meaningful sentences as summary.

    Used for articles whose RSS entry has no useful summary. Falls back to
    a word-boundary-truncated prefix when the text has no sentence
    boundaries (headlines, tickers, fragmented crawl output).
    """
    if not text:
        return ""
    text = _normalise_whitespace(text)
    sentences = [s.strip() for s in _SENTENCE_BOUNDARY_RE.split(text) if s.strip()]
    summary = ""
    for sentence in sentences[:3]:
        candidate = f"{summary} {sentence}".strip()
        if summary and len(candidate) > max_chars:
            break
        summary = candidate
        if len(summary) >= max_chars:
            break
    if not summary:
        summary = text
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0]
    return summary.strip()


def clean_title(raw_title: str, source_name: str) -> str:
    """Remove source name suffixes, HTML entities, excessive punctuation.

    Handles the common crawler/RSS artifacts: ``"Headline | CoinDesk"``,
    ``"Headline - CoinDesk"``, ``"Headline — CoinDesk"``, ``"CoinDesk:
    Headline"``, double spaces, and runs of ``!``/``?``.
    """
    if not raw_title:
        return ""
    title = html.unescape(str(raw_title)).strip()
    source = (source_name or "").strip().lower()
    if source:
        # Match the full source name and its bare domain part (coindesk.com →
        # coindesk). Use whitespace-tolerant variants so display names work:
        # "theblock" matches "The Block", "coindesk" matches "CoinDesk".
        base_parts = [source]
        if "." in source:
            base_parts.append(source.split(".", 1)[0])
        alternatives = "|".join(r"\s*".join(re.escape(ch) for ch in part) for part in base_parts)
        # Trailing suffix: "Headline | source", " - source", " — source", " » source"
        title = re.sub(
            rf"\s*(?:\||-|—|::|»)\s*(?:{alternatives})\s*$",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
        # Leading prefix: "source: Headline", "source | Headline", "source » Headline"
        title = re.sub(
            rf"^\s*(?:{alternatives})\s*(?::|\||»)\s*",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"([!?])\1+", r"\1", title)  # !!! → !, ??? → ?
    title = re.sub(r"\s+([,.;:!?])", r"\1", title)  # "word ," → "word,"
    return title.strip()


def is_english(text: str) -> bool:
    """Simple language detection — check if >80% of words are ASCII.

    Words are 2+ letter sequences (unicode-aware). Fails open (returns
    True) on empty or short (<5 word) text so the pipeline never drops
    content it cannot judge.
    """
    if not text:
        return True
    words = re.findall(r"[^\W\d_]{2,}", text)
    if len(words) < _ENGLISH_MIN_WORDS:
        return True
    ascii_words = sum(1 for word in words if all(ord(c) < 128 for c in word))
    return ascii_words / len(words) >= _ENGLISH_ASCII_THRESHOLD


def _unwrap_result(result: Any) -> Any:
    """Crawl4AI 0.8.x ``arun`` returns a ``CrawlResultContainer``.

    The container is iterable/indexable; unwrap it to the single
    ``CrawlResult`` so callers get the same API across versions.
    """
    if result is None:
        return None
    # A CrawlResult exposes ``success``; the container does not.
    if hasattr(result, "success"):
        return result
    if hasattr(result, "__getitem__"):
        try:
            return result[0]
        except (IndexError, TypeError):
            return result
    return result


def _extract_markdown(result: Any) -> str:
    """Extract the raw markdown text from a CrawlResult.

    Handles both the string-compatible markdown object (0.8.x) and a
    plain-string fallback so the crawler is resilient to API drift.
    """
    md = getattr(result, "markdown", None)
    if md is None:
        return ""
    if isinstance(md, str):
        return md
    raw = getattr(md, "raw_markdown", None)
    if isinstance(raw, str):
        return raw
    # StringCompatibleMarkdown subclasses str in some versions.
    return str(md)


def _doc_id(url: str) -> str:
    """Stable document id for a crawled URL (matches databus hash style)."""
    h = hashlib.sha256(url.encode()).hexdigest()[:12]
    return f"crawl:{h}"


def load_sources() -> dict[str, list[str]]:
    """Load crawl source feed URLs from ``unified_news_sources.json``.

    Returns a mapping of category → list of feed URLs for crawlable source
    types (``rss``/``atom``/``sitemap``/``reddit``/``nitter``). The reddit
    and nitter entries are RSS feeds too, so they are included — the social
    streams are consumed by discovery and pass through the same quality
    gates as article feeds.

    Falls back to the legacy ``crawl_sources.json`` (category → URLs) when
    the unified file is missing or malformed, so old deployments keep
    working. Returns an empty mapping if both are unavailable.
    """
    for path in (_SOURCES_FILE, _LEGACY_SOURCES_FILE):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("sources_load_failed", path=str(path), error=str(e))
            continue
        if not isinstance(data, dict):
            continue
        if "sources" in data:
            # Unified format: [{id, name, url, category, type, ...}, ...]
            grouped: dict[str, list[str]] = {}
            for entry in data.get("sources", []):
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") not in _CRAWLABLE_TYPES:
                    continue
                url = str(entry.get("url") or "")
                if not url.startswith(("http://", "https://")):
                    continue
                grouped.setdefault(str(entry.get("category") or "crypto_news"), []).append(url)
            return grouped
        # Legacy format: {category: [url, ...]}
        return {k: list(v) for k, v in data.items() if isinstance(v, list)}
    return {}


def _article_text(article: dict[str, Any]) -> str:
    """Concatenate the text fields used for relevance matching."""
    parts = (article.get("title"), article.get("summary"), article.get("content"))
    return " ".join(str(p) for p in parts if p)


def _mentions_crypto(text: str) -> bool:
    """True when text mentions crypto tokens/protocols, DeFi, security, or regulation."""
    if not text:
        return False
    return bool(
        _TOKEN_RE.search(text)
        or _PROTOCOL_RE.search(text)
        or _DEFI_RE.search(text)
        or _SECURITY_RE.search(text)
        or _AGENCY_RE.search(text)
        or _REGULATORY_RE.search(text)
    )


def _looks_paywalled(text: str) -> bool:
    """True when the text looks like a locked paywall teaser, not full content."""
    lower = text.lower()
    return any(marker in lower for marker in _PAYWALL_MARKERS)


def score_article_quality(article: dict[str, Any]) -> float:
    """Score article quality 0-1. Filter out low-quality content.

    Components (per spec):
      - Length: >500 chars +0.3, >2000 chars +0.5
      - Has paragraphs: +0.2
      - Not paywalled summary: +0.1
      - Has crypto entities mentioned: +0.2

    ``content`` (when present — i.e. after a crawl) is authoritative: an
    empty crawled content scores 0 so failed extractions are rejected.
    Discovery items without a ``content`` key fall back to the feed
    ``summary`` so pre-crawl scoring still works.
    """
    if article.get("content") is not None:
        content = str(article.get("content") or "")
    else:
        content = str(article.get("summary") or "")
    if not content.strip():
        return 0.0

    score = 0.0
    n = len(content)
    if n > 2000:
        score += 0.5
    elif n > 500:
        score += 0.3
    if "\n\n" in content:
        score += 0.2
    if not _looks_paywalled(content):
        score += 0.1
    if _mentions_crypto(_article_text(article)):
        score += 0.2
    return round(min(score, 1.0), 3)


def is_relevant(article: dict[str, Any]) -> bool:
    """Relevance gate: does the article touch crypto, DeFi, security, or regulation?

    Applied pre-crawl on title + summary so off-topic content (NFT drops,
    pure gaming, general tech) never gets crawled. Any one matching
    category is enough to pass.
    """
    return _mentions_crypto(_article_text(article))


def filter_relevant(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only articles that pass the relevance gate."""
    kept: list[dict[str, Any]] = []
    for item in items:
        if is_relevant(item):
            kept.append(item)
        else:
            log.info("article_irrelevant", url=item.get("url"), title=item.get("title"))
    return kept


def _parse_published(value: str) -> datetime | None:
    """Parse an RSS/Atom publish date into a tz-aware UTC datetime.

    Handles RFC 2822 (most RSS feeds) and ISO 8601 (Atom). Returns None
    when the value is missing or unparseable.
    """
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        dt = None
    if dt is not None:
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def freshness_tier(article: dict[str, Any], *, now: datetime | None = None) -> str:
    """Classify article age: ``fresh`` (<1h), ``recent`` (1-6h), ``stale`` (>6h), ``unknown``."""
    published = _parse_published(str(article.get("published", "")))
    if published is None:
        return "unknown"
    reference = now or datetime.now(UTC)
    age_hours = (reference - published).total_seconds() / 3600.0
    if age_hours < FRESH_AGE_HOURS:
        return "fresh"
    if age_hours <= MAX_ARTICLE_AGE_HOURS:
        return "recent"
    return "stale"


def is_fresh(
    article: dict[str, Any], *, max_age_hours: float = MAX_ARTICLE_AGE_HOURS, now: datetime | None = None
) -> bool:
    """Is the article young enough to ingest?

    ``unknown`` dates pass (we cannot prove staleness) but are
    deprioritised by ``prioritize_by_freshness`` so they only run when
    there are not enough datable items.
    """
    published = _parse_published(str(article.get("published", "")))
    if published is None:
        return True
    reference = now or datetime.now(UTC)
    age_hours = (reference - published).total_seconds() / 3600.0
    return age_hours <= max_age_hours


def prioritize_by_freshness(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort newest-first; items with unparseable dates go last."""

    def _key(item: dict[str, Any]) -> tuple[int, float]:
        published = _parse_published(str(item.get("published", "")))
        if published is None:
            return (0, 0.0)
        return (1, published.timestamp())

    return sorted(items, key=_key, reverse=True)
