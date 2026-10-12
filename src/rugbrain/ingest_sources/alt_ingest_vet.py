"""Footprint vetting rules — REAL broadcast channels only.

Operator directive (2026-10-12): the alt-account ingest must sweep real
broadcast news channels, never groups, user accounts or spam. The rules use
Telegram's own entity flags first (broadcast / scam / fake / restricted), then
a subscriber floor, then a junk-name denylist. Pure functions, no I/O — the
generator (scripts/refresh_alt_channels.py) and the tests share this module.
"""

from __future__ import annotations

from typing import Any

#: our own publishing channels — we post there, we never scrape them
OUR_OWN: tuple[str, ...] = ("rmiupdates", "rmialpha", "rmialerts", "rmi")

#: shapes we actually saw in the wild that are promo/spam, not news
JUNK_NAMES: tuple[str, ...] = (
    "alpha", "callz", "signal", "gem", "sniper", "pump", "moon", "airdrop",
    "presale", "giveaway", "bot", "farm", "vip", "insider", "leak", "whalealert",
)

MIN_SUBS: int = 1000


def vet(name: str, entity: Any) -> str | None:
    """Return a rejection reason, or ``None`` when the channel is accepted."""
    low = str(name).lower()
    if not getattr(entity, "broadcast", False):
        return "not_broadcast"
    if any(getattr(entity, flag, False) for flag in ("scam", "fake", "restricted")):
        return "telegram_flagged"
    if low.startswith(OUR_OWN):
        return "our_own"
    if int(getattr(entity, "participants_count", 0) or 0) < MIN_SUBS:
        return "under_1k"
    if any(junk in low for junk in JUNK_NAMES):
        return "junk_name"
    return None


__all__ = ["JUNK_NAMES", "MIN_SUBS", "OUR_OWN", "vet"]
