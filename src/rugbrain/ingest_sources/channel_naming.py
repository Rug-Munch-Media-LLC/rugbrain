"""Word-boundary tokenisation for Telegram channel names (2026-09-28).

The old classifier matched raw substrings, which is how ``"eth"`` labelled a
channel ``market`` because the word ``health`` contained it. This module is the
whole defence: needles are matched against a *token set* (single-word) or a
*token-joined string* (phrases), never against the raw blob.

Three shapes Telegram actually serves, and how each is handled:

* spaced titles            ``Low Cap Gems``     → ``low cap gems``
* punctuation / hyphens    ``on-chain``, ``Dior's`` → ``on chain``, ``diors``
* unspaced usernames       ``@CatfishcallsbyPoe`` → ``catfishcallsby poe``

Apostrophes are folded to separators first (``Dior's`` → ``dior s``) so a
possessive never hides the word that follows it.
"""

from __future__ import annotations

import re

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_APOS = re.compile("[’'`´]")  # noqa: RUF001 — these ARE the apostrophes we fold
_WORDS = re.compile(r"[a-z0-9]+")


def fold_channel_name(title: str, username: str) -> str:
    """Return the space-joined token stream for a channel title + username."""
    raw = _CAMEL.sub(" ", f"{title or ''} {(username or '').lstrip('@')}")
    return " ".join(_WORDS.findall(_APOS.sub(" ", raw).lower()))


def squash(folded: str) -> str:
    """Separator-free form, for matching unspaced brands (``peckshieldalert``)."""
    return folded.replace(" ", "")


def hit(needle: str, tokens: frozenset[str] | set[str], flat: str) -> bool:
    """Word-bounded match: whole-word for one token, phrase for two or more."""
    if " " in needle:
        return needle in flat
    return needle in tokens


__all__ = ["fold_channel_name", "hit", "squash"]
