"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Publication gate — the single decision every content item passes through.

The pipeline before this gate decides WHAT is good (rerank, dedupe, fact-check).
This module decides WHERE it may go, and nothing else, so the answer is
auditable without reading a publisher:

    skip     not publishable anywhere (floor, duplicate, or no destination)
    feed     archive/mirror only (feed.rugmunch.io) — not broadcast
    channel  broadcast to a Telegram channel — never mirrored to the feed
    both     broadcast AND mirror

Thresholds are explicit constants, not tuned per source. The ladder is the same
for every writer: the same score, category and kind produce the same route
whether the item arrived via Telegram, X, RSS or the firehose. A source family
never outranks another here; source choice happens upstream in the candidate
pool (:mod:`rugbrain.content_candidates`).

Two deliberate exceptions, both documented at the branch:

    * *security* categories (scam/exploit/phishing/…) may broadcast at a lower
      score. This is SEVERITY — retail protection is the editorial mission —
      not source precedence.
    * *operational* kinds (scan/alert) have no feed home: they broadcast or they
      do not publish at all.

A duplicate never broadcasts again. It may update its feed entry when the newer
copy scores at or above the feed floor, because a corrected/expanded version is
evidence worth archiving but must not become a second post.
"""

from __future__ import annotations

import math
from typing import Final

#: Below this, an item is not worth publishing anywhere.
SCORE_FLOOR: Final = 0.35
#: Feed-only band starts at the floor; the constant is named for readability.
FEED_MIN: Final = SCORE_FLOOR
#: Broadcast bar for editorial categories.
CHANNEL_MIN: Final = 0.65
#: Broadcast + mirror bar for editorial categories.
BOTH_MIN: Final = 0.85
#: Broadcast bar for scam/security items (severity escalation, not precedence).
SECURITY_CHANNEL_MIN: Final = 0.45
#: Broadcast + mirror bar for scam/security items.
SECURITY_BOTH_MIN: Final = 0.75

#: Categories that carry a protection obligation and therefore escalate early.
SECURITY_CATEGORIES: Final = frozenset(
    {"scam", "security", "exploit", "hack", "phishing", "vulnerability"}
)

#: Kinds that are operational output: channel or nothing.
OPERATIONAL_KINDS: Final = frozenset({"scan", "alert", "scanner"})

#: Categories the ladder may fully judge. Anything else is feed-only: an item
#: we cannot classify is archived, never broadcast on a guess.
KNOWN_CATEGORIES: Final = (
    frozenset(
        {
            "news",
            "research",
            "analysis",
            "market",
            "onchain",
            "social",
            "x_social",
            "recap",
            "story",
        }
    )
    | SECURITY_CATEGORIES
)

ROUTES: Final = ("skip", "feed", "channel", "both")


def decide(score: float, category: str, kind: str, duplicate: bool) -> dict[str, str]:
    """Return the publication route and the reason for it.

    Args:
        score: importance/quality in 0..1 from the rerank stage.
        category: editorial category (``news``, ``research``, ``scam``, …).
        kind: item kind (``story``, ``scan``, ``alert``, …).
        duplicate: True if this story was already published (cross-channel dup).

    Returns:
        ``{"route": "skip"|"feed"|"channel"|"both", "reason": str}``. Both keys
        are always present; the reason quotes the threshold that decided it.
    """
    value = float(score)
    if not math.isfinite(value):
        return {"route": "skip", "reason": f"invalid score {score!r}: not a finite number"}

    cat = (category or "").strip().lower()
    item_kind = (kind or "").strip().lower() or "story"

    # Duplicates are already published. Re-broadcasting is the failure mode this
    # whole pool exists to prevent; only the archive may be refreshed.
    if duplicate:
        if value >= FEED_MIN:
            return {
                "route": "feed",
                "reason": (
                    f"duplicate: feed-only update (score {value:.2f} >= feed {FEED_MIN:.2f}); "
                    "already broadcast, no re-post"
                ),
            }
        return {
            "route": "skip",
            "reason": f"duplicate below feed floor: {value:.2f} < {FEED_MIN:.2f}",
        }

    if value < SCORE_FLOOR:
        return {
            "route": "skip",
            "reason": f"below floor: score {value:.2f} < {SCORE_FLOOR:.2f}",
        }

    security = cat in SECURITY_CATEGORIES
    channel_min = SECURITY_CHANNEL_MIN if security else CHANNEL_MIN
    both_min = SECURITY_BOTH_MIN if security else BOTH_MIN

    if value >= both_min:
        route, basis = "both", f"score {value:.2f} >= both {both_min:.2f}"
    elif value >= channel_min:
        route, basis = "channel", f"score {value:.2f} >= channel {channel_min:.2f}"
    else:
        route, basis = "feed", f"score {value:.2f} >= feed {FEED_MIN:.2f}"
    ladder = "security" if security else "editorial"

    # Unclassified content is archived, never broadcast: a wrong post cannot be
    # retracted, a missing post can be picked up next cycle.
    if cat not in KNOWN_CATEGORIES:
        held = "" if route == "feed" else f"; {ladder} ladder said {route}: {basis}"
        return {
            "route": "feed",
            "reason": (f"unknown category {cat or '<empty>'!r}: feed-only until classified{held}"),
        }

    if item_kind in OPERATIONAL_KINDS:
        if route == "feed":
            return {
                "route": "skip",
                "reason": (f"{item_kind} has no feed home and misses the channel bar: {basis}"),
            }
        return {
            "route": "channel",
            "reason": f"{item_kind}: channel only, never mirrored ({basis})",
        }

    return {"route": route, "reason": f"{ladder} ladder: {basis}"}
