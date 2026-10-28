"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

The three named editions — title, send time, brief, paragraph skeleton.

One table, no second copy anywhere: the writer prompt, the site document and
the scheduler all read these keys. Titles are exact strings (em dash, capital
letters) because they are the brand line a reader sees, not a slug.
"""

from __future__ import annotations

from typing import Any

EDITIONS: dict[str, dict[str, Any]] = {
    "morning": {
        "key": "morning",
        "prompt": "edition_morning",
        "title": "Rug Munch Intelligence — The Morning Rug",
        "send_utc": "06:30",
        "brief": (
            "What moved and what broke while the US and Europe were closed: "
            "overnight flows, rug pulls and exploits, and the wallets that "
            "carried them."
        ),
        "skeleton": (
            "Overnight risk tape",
            "Rugs and exploits",
            "Wallets that moved size",
            "What the desk watches today",
        ),
        "paragraphs": (3, 4),
        "teaser_chars": 280,
    },
    "midday": {
        "key": "midday",
        "prompt": "edition_midday",
        "title": "Rug Munch Intelligence — Midday Market Report",
        "send_utc": "12:30",
        "brief": (
            "What is moving in the live session: the intraday movers, the "
            "catalyst doing the work, and the risk building under it."
        ),
        "skeleton": (
            "Session movers",
            "Catalyst and alpha",
            "Risk building now",
        ),
        "paragraphs": (2, 3),
        "teaser_chars": 280,
    },
    "evening": {
        "key": "evening",
        "prompt": "edition_evening",
        "title": "Rug Munch Intelligence — Evening Market Closing Report",
        "send_utc": "21:30",
        "brief": (
            "How the day closed: realised PnL on the desk's published calls, "
            "the dominant narrative, and the scheduled tape that hands over "
            "to tomorrow."
        ),
        "skeleton": (
            "Day close",
            "Desk PnL and calls",
            "Dominant narrative",
            "Tomorrow's tape",
        ),
        "paragraphs": (3, 4),
        "teaser_chars": 280,
    },
}


def edition(key: str) -> dict[str, Any]:
    """One edition's spec. Unknown keys raise — a typo never renders a brief."""
    try:
        return EDITIONS[str(key).strip().lower()]
    except KeyError as exc:
        raise KeyError(f"unknown edition {key!r}; known: {sorted(EDITIONS)}") from exc


def edition_keys() -> tuple[str, ...]:
    """The three edition keys, in publication order."""
    return tuple(EDITIONS)
