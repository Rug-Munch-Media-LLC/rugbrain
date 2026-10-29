"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Promotion lexicon. Terms are matched on word boundaries so "airdrop" in a
neutral market wrap does not behave like "AIRDROP LIVE — join now". A hit is a
signal with a weight, never a conclusion.
"""

from __future__ import annotations

import re
from functools import lru_cache

#: Promises of outcome. Regulators treat these as the strongest red flag.
GUARANTEE_TERMS: tuple[str, ...] = (
    "guaranteed",
    "guaranteed profit",
    "guaranteed returns",
    "risk free",
    "risk-free",
    "cannot lose",
    "can't lose",
    "100x",
    "1000x",
    "x100",
    "to the moon",
    "sure thing",
    "passive income guaranteed",
)

#: Sale mechanics tied to a specific contract.
PRESALE_TERMS: tuple[str, ...] = (
    "presale",
    "pre-sale",
    "private sale",
    "fair launch",
    "whitelist",
    "allowlist",
    "public sale",
    "token sale",
    "ido",
    "launchpad",
)

#: Time pressure that removes the viewer's chance to check the contract.
URGENCY_TERMS: tuple[str, ...] = (
    "last chance",
    "ending soon",
    "minutes left",
    "before it's too late",
    "before its too late",
    "only today",
    "act now",
    "don't miss",
    "dont miss",
    "first 100",
    "early birds",
)

#: Content that only makes sense as a paid placement.
PROMO_TERMS: tuple[str, ...] = (
    "presale",
    "airdrop",
    "whitelist",
    "allowlist",
    "contract address",
    "contract:",
    "join telegram",
    "join our telegram",
    "link below",
    "links below",
    "buy now",
    "sign up",
    "use my code",
    "referral code",
    "affiliate",
    "sponsor",
)

#: Any one of these satisfies the disclosure requirement.
DISCLOSURE_MARKERS: tuple[str, ...] = (
    "paid promotion",
    "paid partnership",
    "paid advertisement",
    "sponsored by",
    "sponsored video",
    "this video is sponsored",
    "#ad",
    "#sponsored",
    "includes paid promotion",
    "advertisement:",
    "commission may be earned",
    "affiliate disclosure",
)


@lru_cache(maxsize=64)
def _pattern(terms: tuple[str, ...]) -> re.Pattern[str]:
    joined = "|".join(re.escape(term).replace(r"\ ", r"\s+") for term in terms)
    return re.compile(rf"(?<![A-Za-z0-9])(?:{joined})(?![A-Za-z0-9])", re.IGNORECASE)


def find_terms(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    """Return the terms present in ``text``, in first-seen order."""
    if not text or not terms:
        return ()
    found: list[str] = []
    for match in _pattern(terms).finditer(text):
        value = " ".join(match.group(0).lower().split())
        if value not in found:
            found.append(value)
    return tuple(found)


def has_disclosure(text: str, paid_promo_label: bool | None = None) -> bool:
    """True when YouTube's paid-promotion label or an in-text disclosure exists."""
    if paid_promo_label:
        return True
    return bool(find_terms(text, DISCLOSURE_MARKERS))
