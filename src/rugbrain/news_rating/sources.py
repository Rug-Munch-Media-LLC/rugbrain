"""Source credibility: reputation table, aliases, and lookup helpers."""

from __future__ import annotations

import logging
import re

from rugbrain.news_rating.axes import (
    REPUTATION_ESTABLISHED_MIN,
    REPUTATION_INDEPENDENT_MIN,
    _clamp,
)

logger = logging.getLogger(__name__)

#: domain → (0-100 reputation, tier label). Unknown domains default low.
# The expanded 100+ source database lives in ``news_sources`` and is merged
# here so the single ``SOURCE_REPUTATION`` dict remains the lookup surface.
try:
    from rugbrain.news_sources import merged_source_reputation

    SOURCE_REPUTATION: dict[str, tuple[int, str]] = merged_source_reputation()
except ImportError:  # pragma: no cover — fallback keeps the module importable
    logger.warning("news_sources unavailable; using built-in reputation fallback")
    SOURCE_REPUTATION = {
        "reuters.com": (96, "established"),
        "bloomberg.com": (96, "established"),
        "apnews.com": (96, "established"),
        "coindesk.com": (95, "established"),
        "ft.com": (95, "established"),
        "wsj.com": (95, "established"),
        "nytimes.com": (95, "established"),
        "theblock.co": (94, "established"),
        "theguardian.com": (92, "established"),
        "decrypt.co": (92, "established"),
        "cointelegraph.com": (90, "established"),
        "blockworks.co": (90, "established"),
        "thedefiant.io": (88, "established"),
        "axios.com": (85, "established"),
        "forbes.com": (85, "established"),
        "bitcoinmagazine.com": (80, "established"),
        "beincrypto.com": (72, "independent"),
        "cryptonews.com": (70, "independent"),
        "news.bitcoin.com": (70, "independent"),
        "ambcrypto.com": (70, "independent"),
        "u.today": (68, "independent"),
        "dailyhodl.com": (66, "independent"),
        "zycrypto.com": (64, "independent"),
        "coinjournal.net": (62, "independent"),
    }

#: source *name* aliases → domain (fallback when the URL is absent).
SOURCE_NAME_ALIASES: dict[str, str] = {
    "coindesk": "coindesk.com",
    "the block": "theblock.co",
    "decrypt": "decrypt.co",
    "cointelegraph": "cointelegraph.com",
    "blockworks": "blockworks.co",
    "the defiant": "thedefiant.io",
    "reuters": "reuters.com",
    "bloomberg": "bloomberg.com",
    "associated press": "apnews.com",
    "forbes": "forbes.com",
    "the guardian": "theguardian.com",
    "financial times": "ft.com",
    "wall street journal": "wsj.com",
    "new york times": "nytimes.com",
    "axios": "axios.com",
    "bitcoin magazine": "bitcoinmagazine.com",
    "beincrypto": "beincrypto.com",
    "cryptonews": "cryptonews.com",
    "bitcoin.com news": "news.bitcoin.com",
    "u.today": "u.today",
    "daily hodl": "dailyhodl.com",
    "zycrypto": "zycrypto.com",
    "coinjournal": "coinjournal.net",
    "ambcrypto": "ambcrypto.com",
}
try:  # merge the expanded alias table (extended wins on conflict)
    from rugbrain.news_sources import EXTENDED_SOURCE_ALIASES

    SOURCE_NAME_ALIASES.update(EXTENDED_SOURCE_ALIASES)
except ImportError:  # pragma: no cover
    logger.debug("extended source aliases unavailable; using base table")

DEFAULT_SOURCE_REPUTATION = 45


def domain_from_url(url: str) -> str:
    """Extract a lowercase bare domain (no scheme, port, or path) from *url*."""
    if not url:
        return ""
    match = re.match(r"(?:https?://)?(?:www\.)?([^/:?#]+)", url.strip())
    if not match:
        return ""
    return match.group(1).lower()


def source_reputation_score(source: str, url: str = "", override: int | None = None) -> int:
    """0-100 reputation score for a source name/domain.

    Priority: explicit ``override`` > domain lookup > source-name alias
    lookup > default (unknown blog).
    """
    if override is not None:
        return _clamp(override)
    domain = domain_from_url(url)
    if domain in SOURCE_REPUTATION:
        return SOURCE_REPUTATION[domain][0]
    name = (source or "").strip().lower()
    if name in SOURCE_REPUTATION:
        return SOURCE_REPUTATION[name][0]
    alias = SOURCE_NAME_ALIASES.get(name)
    if alias and alias in SOURCE_REPUTATION:
        return SOURCE_REPUTATION[alias][0]
    return DEFAULT_SOURCE_REPUTATION


def source_reputation_label(score: int) -> str:
    """Tier label for a source reputation score."""
    if score >= REPUTATION_ESTABLISHED_MIN:
        return "established"
    if score >= REPUTATION_INDEPENDENT_MIN:
        return "independent"
    return "unknown"
