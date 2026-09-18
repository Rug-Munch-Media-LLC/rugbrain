"""Corroboration: independent-source counting and scoring."""

from __future__ import annotations

import re
from typing import Any

from rugbrain.news_rating.sources import SOURCE_REPUTATION, domain_from_url

_CROSS_REF_KEY_RE = re.compile(
    r"\b(?:lost|stolen|hacked|exploited|drained|drained|attack)\b", re.IGNORECASE
)


def corroboration_score(corroborating_sources: int | None) -> int:
    """0-100: more independent sources → higher corroboration.

    ``None``/unknown and 0 both score low; 5+ sources saturate.
    """
    if corroborating_sources is None:
        return 30
    n = max(0, int(corroborating_sources))
    if n >= 5:
        return 92
    if n == 4:
        return 85
    if n == 3:
        return 75
    if n == 2:
        return 60
    if n == 1:
        return 45
    return 30


def cross_reference_count(article: dict[str, Any]) -> int:
    """Estimate how many *trusted* independent sources report the event.

    Counts distinct trusted domains from the article's ``source_urls``,
    ``references``, ``related_articles`` and ``citations`` lists, and treats
    an explicit ``corroborating_sources`` field as authoritative when it is
    higher. ``aggregated`` articles count as at least two corroborating
    sources. A domain is trusted when it has an established/independent
    rating in ``SOURCE_REPUTATION``; unknown domains still count (an
    unknown blog corroborating a wire report is weak, so only trusted
    domains push the count past one).
    """
    explicit = article.get("corroborating_sources")
    if isinstance(explicit, int) and explicit > 0:
        return max(0, int(explicit))

    seen: set[str] = set()
    trusted: set[str] = set()
    for key in ("source_urls", "references", "related_articles", "citations"):
        for item in article.get(key) or []:
            if isinstance(item, str) and item.strip():
                domain = domain_from_url(item)
                if domain:
                    seen.add(domain)
                    if domain in SOURCE_REPUTATION:
                        trusted.add(domain)
            elif isinstance(item, dict):
                for sub in (item.get("url"), item.get("source"), item.get("title")):
                    if isinstance(sub, str) and sub.strip():
                        domain = domain_from_url(sub)
                        if domain:
                            seen.add(domain)
                            if domain in SOURCE_REPUTATION:
                                trusted.add(domain)
    count = len(seen)
    if article.get("aggregated"):
        count = max(count, 2)
    # Without at least one trusted domain, a lone unknown source is weak
    # (but an explicitly-aggregated article already counts as >= 2 sources).
    if count >= 1 and not trusted and not article.get("aggregated"):
        count = min(count, 1)
    return count


def cross_reference_score(article: dict[str, Any]) -> int:
    """0-100: independent trusted coverage of the same event.

    Thin wrapper over ``cross_reference_count`` so callers can compute the
    corroboration axis from the article dict alone.
    """
    return corroboration_score(cross_reference_count(article))
