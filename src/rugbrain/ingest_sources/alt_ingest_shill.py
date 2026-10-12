"""Shill-campaign detection for the rejected/promo channel watchlist.

The channels the vetting REJECTS for the news sweep (junk-name, sub-1K,
Telegram-flagged, promo shapes) are exactly the population where shilling
lives. This module turns their recent posts into scam-intel records so the
scanner and the announcer can act on them.

Pure functions only — no I/O — so they are trivially testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

#: the classic shill vocabulary. Cheap, high-precision, deliberately small.
SHILL_PATTERNS: Final[tuple[str, ...]] = (
    "100x", "1000x", "x100", "guaranteed", "guaranteed returns", "risk free",
    "risk-free", "presale", "pre-sale", "airdrop", "whitelist", "private sale",
    "dm me", "send me btc", "send eth", "double your", "pump", "moon",
    "to the moon", "gem", "alpha group", "vip group", "insider", "leak",
    "secret", "don't miss", "act now", "limited spots", "copy this wallet",
    "call telegram", "telegram group", "rug", "exit liquidity", "honeypot",
    "approve and send", "revoke approval", "drainer", "contract address",
)

#: reward/bullish framing that turns neutral news into promotion
_PROMO_RE: Final = re.compile(
    r"\b(?:buy|accumulate|load up|long|ape|enter)\b[^.!?\n]{0,40}\b(?:now|today|before|early)\b",
    re.IGNORECASE,
)

#: a bare token that looks like a contract address (ERC-20-ish / solana-ish)
_ADDRESS_RE: Final = re.compile(r"\b(?:0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")

#: ticker + (pump) style pair
_TICKER_RE: Final = re.compile(r"\$([A-Z]{2,10})\b")


@dataclass(frozen=True)
class ShillSignal:
    """One shill finding: which patterns hit, plus the scored risk."""

    reasons: tuple[str, ...]
    addresses: tuple[str, ...] = ()
    tickers: tuple[str, ...] = ()
    risk: int = 0
    categories: tuple[str, ...] = field(default=())


def analyze(text: str, *, min_risk: int = 2) -> ShillSignal | None:
    """Score a post for shill signals. Returns None when below ``min_risk``.

    risk += 2 per reward-guarantee phrase, +1 per urgency/promo phrase,
    +2 when a bare contract address is posted alongside any hit,
    +1 per distinct promo-language match.
    """
    body = (text or "").strip()
    if not body:
        return None
    low = body.lower()

    hits: list[str] = []
    risk = 0
    for pat in SHILL_PATTERNS:
        if pat in low:
            hits.append(pat)
            risk += 2 if any(k in pat for k in ("x", "guaranteed", "risk", "double", "pump", "moon")) else 1

    promo = bool(_PROMO_RE.search(body))
    if promo:
        risk += 1

    addresses = tuple(dict.fromkeys(_ADDRESS_RE.findall(body)))
    tickers = tuple(dict.fromkeys(m.upper() for m in _TICKER_RE.findall(body)))
    if addresses and hits:
        risk += 2
    if tickers and hits:
        risk += 1

    if risk < min_risk:
        return None

    categories = []
    if addresses and hits:
        categories.append("contract_promo")
    if any(p in low for p in ("guaranteed", "risk free", "risk-free", "x100", "100x", "1000x")):
        categories.append("guaranteed_returns")
    if any(p in low for p in ("presale", "airdrop", "whitelist", "private sale")):
        categories.append("presale_promo")
    if promo or any(p in low for p in ("dm me", "call telegram", "telegram group", "vip group", "alpha group")):
        categories.append("telegram_cta")
    if any(p in low for p in ("rug", "exit liquidity", "honeypot", "drainer", "approve and send", "revoke approval")):
        categories.append("rug_or_drain")

    return ShillSignal(
        reasons=tuple(dict.fromkeys(hits)),
        addresses=addresses,
        tickers=tickers,
        risk=risk,
        categories=tuple(categories),
    )


__all__ = ["SHILL_PATTERNS", "ShillSignal", "analyze"]
