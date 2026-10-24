"""Channel taxonomy for the Telegram user-account ingester (2026-09-28).

Replaces the keyword-substring table that shipped in ``telegram_userbot.py``.
Measured on the live account: 219 dialogs, and ``"eth"`` substring-matched
*health*, … so generic channels were labelled ``market``; call channels
(``Catfish Calls by Poe``, ``Milady Calls``, ``Dior's Alpha``, ``Marcell's
Trenches``) fell inconsistently into ``shiller``/``general``.

**Emitted values are unchanged on purpose.** ``news_filter.TOPICAL_CATEGORIES``
(the single classifier shared by the ingest gate and the noise-cleanup script)
knows only ``scam_caller | security | shiller | news | data | market | …``.
Rule names that are *more* expressive than the stored vocabulary
(``alpha_caller``, ``research``) are aliased onto the existing values, so a new
rule can never silently demote a doc to ``noise``.

Matching is word-bounded (see :mod:`channel_naming`) and ordered
most-specific-first; a soft category preference for sweep tie-breaks lives in
:mod:`rugbrain.ingest_sources.sweep_order`.
"""

from __future__ import annotations

from rugbrain.ingest_sources.channel_naming import fold_channel_name, hit, squash
from rugbrain.ingest_sources.channel_taxonomy_rules import (
    ALPHA_CALLER_NEEDLES,
    DATA_NEEDLES,
    MARKET_NEEDLES,
    NEWS_NEEDLES,
    RESEARCH_NEEDLES,
    SCAM_CALLER_NEEDLES,
    SECURITY_NEEDLES,
)

#: Rule name -> stored category value emitted in payloads. Keys not present
#: here are emitted verbatim.
CATEGORY_ALIASES: dict[str, str] = {
    "alpha_caller": "shiller",
    "research": "news",
    "longtail": "general",
}

#: Brands whose real channel names are unspaced compounds — ``PeckShieldAlert``,
#: ``BlockSecAlert`` — so no word rule can ever see them. Matched against the
#: separator-free blob, which is safe precisely because every entry is a long,
#: specific name: the ``eth``/``health`` bug class cannot recur because ``eth``
#: is not a brand.
BRANDS: dict[str, str] = {
    "peckshield": "security",
    "blocksec": "security",
    "certik": "security",
    "slowmist": "security",
    "immunefi": "security",
    "openpost": "news",
    "watcherguru": "news",
    "news_crypto": "news",
    "nansen": "data",
    "glassnode": "data",
    "arkham": "data",
    "dexscreener": "data",
    "coingecko": "data",
    "defillama": "data",
    "screamai": "data",
}

#: Importance multiplier per *emitted* category (values unchanged).
CATEGORY_WEIGHT: dict[str, float] = {
    "scam_caller": 1.4,
    "security": 1.35,
    "shiller": 1.1,
    "news": 1.0,
    "data": 0.95,
    "market": 0.8,
    "general": 0.7,
}

#: Ordered most-specific-first, first match wins. The needle tables live in
#: channel_taxonomy_rules; CATEGORY_ALIASES maps rule names onto payload values.
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("security", SECURITY_NEEDLES),
    ("scam_caller", SCAM_CALLER_NEEDLES),
    ("alpha_caller", ALPHA_CALLER_NEEDLES),
    ("research", RESEARCH_NEEDLES),
    ("data", DATA_NEEDLES),
    ("market", MARKET_NEEDLES),
    ("news", NEWS_NEEDLES),
]


def _brand(folded: str) -> str | None:
    """Category of a known brand in the separator-free blob, else None."""
    packed = squash(folded)
    for brand, cat in BRANDS.items():
        if brand in packed:
            return cat
    return None


def categorize(title: str, username: str) -> str:
    """Classify one channel. Public signature preserved from the userbot."""
    folded = fold_channel_name(title, username)
    tokens = frozenset(folded.split())
    brand = _brand(folded)
    for cat, needles in CATEGORY_RULES:
        if any(hit(n, tokens, folded) for n in needles):
            if cat == "market" and brand is not None:
                return brand  # a ticker word loses to a real brand (screamAI)
            return CATEGORY_ALIASES.get(cat, cat)
    return brand or CATEGORY_ALIASES["longtail"]


__all__ = [
    "BRANDS",
    "CATEGORY_ALIASES",
    "CATEGORY_RULES",
    "CATEGORY_WEIGHT",
    "categorize",
]
