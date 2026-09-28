"""Category / severity vocabulary for headline styling. Data, not logic.

Extending it: add a key to :data:`CATEGORY_PREFIX` or :data:`SEVERITY_CUE`
and every renderer picks it up. Unknown categories fall back to
:data:`DEFAULT_CATEGORY` and unknown severities to no cue at all.
"""

from __future__ import annotations

#: Canonical category keys. Aliases map noisy upstream labels onto these.
DEFAULT_CATEGORY = "news"

CATEGORY_ALIASES: dict[str, str] = {
    "rugpull": "rug",
    "rug_pull": "rug",
    "rug-pull": "rug",
    "scam": "rug",
    "fraud": "rug",
    "phishing": "scam",
    "honeypot": "scam",
    "exploit": "hack",
    "hack": "hack",
    "hacking": "hack",
    "breach": "hack",
    "attack": "hack",
    "vulnerability": "hack",
    "sec": "regul",
    "sec_ruling": "regul",
    "regulation": "regul",
    "regulatory": "regul",
    "enforcement": "regul",
    "lawsuit": "regul",
    "markets": "market",
    "market_data": "market",
    "price": "market",
    "trading": "market",
    "liquidation": "market",
    "macro": "market",
}

#: Human label rendered before the title, per canonical category.
CATEGORY_PREFIX: dict[str, str] = {
    "rug": "RUG",
    "scam": "SCAM",
    "hack": "EXPLOIT",
    "news": "NEWS",
    "market": "MARKET",
    "regul": "REGULATORY",
}

#: Emoji pool per canonical category. Order is the rotation order.
CATEGORY_EMOJI: dict[str, tuple[str, ...]] = {
    "rug": ("🚨", "🔴", "⚠️", "🩸"),
    "scam": ("🪤", "🕸️", "🎣", "🧿"),
    "hack": ("🕵️", "💀", "🧨", "🛠️"),
    "news": ("📰", "🗞️", "📡", "🔔"),
    "market": ("📈", "📉", "📊", "💹"),
    "regul": ("⚖️", "🏛️", "📜", "🏦"),
}

#: Short credibility cue per severity, appended in parentheses.
SEVERITY_CUE: dict[str, str] = {
    "critical": "🔴 CONFIRMED",
    "high": "🟠 UNCONFIRMED",
    "medium": "🟡 SINGLE SOURCE",
    "low": "🟢 UNVERIFIED",
}

#: Severity words that imply a category when the payload carries none.
SEVERITY_CATEGORY: dict[str, str] = {
    "critical": "hack",
    "high": "scam",
    "medium": "news",
    "low": "news",
}
