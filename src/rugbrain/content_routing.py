"""Cream-of-the-crop routing: which channel, how many, and why.

Answers WHERE an item may go. Whether it is publishable is
:mod:`rugbrain.content_gate`'s decision, and this module CALLS it
(:func:`content_gate.decide`) rather than re-implementing the ladder. Both
reasons are returned, so a "no" is attributable to the right owner.

    tier      threshold                    channel   max/day
    urgent    value >= 0.85                alerts    2      (mirrored to main)
    lead      value >= 0.70                main      3      (mirrored to site)
    whales    value >= 0.62 + on-chain     whales    3
              magnitude >= 0.50
    alpha     value >= 0.62 + research/    alpha     4
              onchain/analysis
    news      value >= 0.55                news      6
    site      value >= 0.35                site     25      (daily edition)

Order is precedence, not severity: the FIRST tier an item clears claims it, so
one story never occupies two channels on the same day. Whales sits above alpha
because it is the more specific rule. A tier at its daily cap falls THROUGH to
the next matching tier rather than dropping the item — a full channel is a
scheduling fact, not a quality verdict. The gate still wins: ``feed`` (below the
broadcast bar, unknown category, duplicate) sends the item to the site archive
whatever the tier wanted, and the reason says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rugbrain import content_gate, content_value_components
from rugbrain.content_history import SeenLedger
from rugbrain.content_value import score_candidate

#: Channels a tier may target. Telegram channel keys match the publisher's draft
#: buckets (``main``, ``alerts``, ``news``); ``site`` is the daily edition.
CHANNELS = ("alerts", "main", "alpha", "whales", "news", "site")


@dataclass(frozen=True)
class Tier:
    """One routing tier: a bar, a destination, and a daily budget."""

    name: str
    min_value: float
    channel: str
    max_per_day: int
    categories: frozenset[str] | None = None
    min_onchain: float = 0.0
    mirror: str = ""

    def matches(self, value: float, category: str, magnitude: float) -> bool:
        """True when this tier's bar is the one the item clears."""
        if value < self.min_value:
            return False
        if self.categories is not None and category not in self.categories:
            return False
        return magnitude >= self.min_onchain


TIERS: tuple[Tier, ...] = (
    Tier(
        "urgent",
        0.85,
        "alerts",
        2,
        mirror="main",
    ),
    Tier(
        "lead",
        0.70,
        "main",
        3,
        mirror="site",
    ),
    Tier(
        "whales",
        0.62,
        "whales",
        3,
        categories=frozenset({"onchain"}),
        min_onchain=0.50,
    ),
    Tier(
        "alpha",
        0.62,
        "alpha",
        4,
        categories=frozenset({"research", "onchain", "analysis"}),
    ),
    Tier("news", 0.55, "news", 6),
    Tier("site", 0.35, "site", 25),
)


def _tier_for(
    value: float, category: str, magnitude: float, seen: SeenLedger, day: str
) -> tuple[Tier | None, str]:
    """First tier the item clears that still has budget today, plus the reason."""
    held: list[str] = []
    for tier in TIERS:
        if not tier.matches(value, category, magnitude):
            continue
        used = seen.posted(tier.channel, day)
        if used >= tier.max_per_day:
            held.append(f"{tier.name} full ({used}/{tier.max_per_day})")
            continue
        reason = f"{tier.name}: value {value:.2f} >= {tier.min_value:.2f}"
        if held:
            reason += f"; passed over {'; '.join(held)}"
        return tier, reason
    if held:
        return None, f"all eligible tiers full: {'; '.join(held)}"
    return None, f"no tier matches: value {value:.2f} below {TIERS[-1].min_value:.2f}"


def _decision(
    tier: str,
    channel: str,
    mirror: str,
    daily_edition: bool,
    route: str,
    value: float,
    reason: str,
    gate: dict[str, str],
) -> dict[str, Any]:
    """One routing decision in one shape, so no branch can omit a field."""
    return {
        "tier": tier,
        "channel": channel,
        "mirror": mirror,
        "daily_edition": daily_edition,
        "route": route,
        "value": value,
        "reason": reason,
        "gate_route": gate["route"],
        "gate_reason": gate["reason"],
    }


def route_item(
    story: dict[str, Any],
    *,
    day: str,
    seen: SeenLedger,
    value: float | None = None,
    duplicate: bool = False,
    now: Any = None,
) -> dict[str, Any]:
    """Route one candidate. Returns the tier, channel, gate route and both reasons.

    *value* is the item's score; when omitted it is computed here so a caller
    passing a raw story still gets an explainable decision. *duplicate* must be
    True for anything a repeat check has already published — the gate then
    routes it as an archive update and never a second broadcast.
    """
    scored = score_candidate(story, now=now) if value is None else {"value": float(value)}
    score = round(min(1.0, max(0.0, float(scored["value"]))), 4)
    category = str(story.get("category") or "").strip().lower()
    kind = str(story.get("kind") or "story").strip().lower() or "story"
    magnitude = content_value_components.onchain_magnitude(story.get("metadata") or {})

    gate = content_gate.decide(score, category, kind, duplicate)
    if gate["route"] == "skip":
        return _decision("skip", "", "", False, "skip", score, f"gate: {gate['reason']}", gate)

    tier, reason = _tier_for(score, category, magnitude, seen, day)
    if gate["route"] == "feed":
        # The gate refuses broadcast: archive it, whatever the tier wanted.
        return _decision(
            tier.name if tier else "site",
            "site",
            "",
            True,
            "feed",
            score,
            f"gate holds it to the archive: {gate['reason']}; tier: {reason}",
            gate,
        )
    if tier is None:
        return _decision("skip", "", "", False, "skip", score, reason, gate)
    # ``mirror`` is the GATE's feed mirror (only "both" earns it); the site
    # daily edition is the TIER's, so the two are reported separately and a
    # channel-only item still appears in the day's edition.
    mirror = tier.mirror if gate["route"] == "both" and tier.mirror != "site" else ""
    return _decision(
        tier.name,
        tier.channel,
        mirror,
        tier.mirror == "site" or tier.channel == "site",
        gate["route"],
        score,
        f"{reason}; gate {gate['route']}: {gate['reason']}",
        gate,
    )


__all__ = ["CHANNELS", "TIERS", "Tier", "route_item"]
