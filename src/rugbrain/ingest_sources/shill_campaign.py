"""Causal shill-campaign clustering.

The lexicon in :mod:`alt_ingest_shill` scores ONE message. Campaigns are a
PROPAGATION phenomenon: the same pitch pasted across many channels inside a
window. This module groups messages into campaigns with a strict look-ahead
guard — a message is only ever scored against history whose timestamp is
strictly earlier than its own, so a replay of the corpus produces exactly the
decisions the live pipeline would have produced.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

WINDOW_SECONDS: Final = 24 * 3600
MIN_CHANNELS: Final = 3

_PREFIX: Final = re.compile(r"^\[TG/[^\]]+\]\s*")
_URL: Final = re.compile(r"https?://\S+")
_ADDR: Final = re.compile(r"\b(?:0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")
_NONWORD: Final = re.compile(r"[^a-z0-9 ]+")
_SPACE: Final = re.compile(r"\s+")


@dataclass(frozen=True)
class CampaignSignal:
    """A coordinated burst: WHO/WHAT is being pushed and how wide the burst is."""

    campaign_id: str
    fingerprint: str
    channels: int
    score: float
    reasons: tuple[str, ...]


def to_epoch(when: str | int | float | None) -> int:
    """Accept an ISO-8601 string or an epoch; return epoch seconds (0 when unknown)."""
    if isinstance(when, (int, float)):
        return int(when)
    if not when:
        return 0
    try:
        return int(datetime.fromisoformat(str(when).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def fingerprint(text: str) -> str:
    """Copy-paste fingerprint: drop channel prefix, URLs and addresses, keep words."""
    body = _PREFIX.sub("", text or "").lower()
    body = _SPACE.sub(" ", _NONWORD.sub(" ", _ADDR.sub(" ", _URL.sub(" ", body)))).strip()
    return " ".join(body.split()[:40])


def campaign_id(text: str, when: str | int | float | None) -> str:
    """Stable, causal campaign id: fingerprint + UTC day bucket of first sighting."""
    ts = to_epoch(when)
    bucket = ts // 86400 if ts else 0
    raw = f"{fingerprint(text)}:{bucket}"
    # Deterministic identifier, not a security primitive: campaign ids must stay
    # stable across runs. usedforsecurity=False says so in code and keeps a
    # FIPS-mode interpreter from refusing the call.
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def score_campaign(
    channel: str,
    text: str,
    when: str | int | float | None,
    history: Iterable[tuple[str, str, str | int | float]],
    *,
    min_channels: int = MIN_CHANNELS,
    window: int = WINDOW_SECONDS,
) -> CampaignSignal | None:
    """Score a message against PRIOR messages only.

    ``history`` holds ``(channel, text, when)`` triples. Anything with a
    timestamp >= this message's timestamp is discarded before scoring — the
    look-ahead guard. Returns ``None`` when the burst is narrower than
    ``min_channels`` distinct channels inside ``window`` seconds.
    """
    ts = to_epoch(when)
    want = fingerprint(text)
    if not want or not ts:
        return None
    seen: set[str] = set()
    for h_channel, h_text, h_when in history:
        h_ts = to_epoch(h_when)
        if h_ts >= ts:  # LOOK-AHEAD GUARD: future (or same-instant) data is invisible
            continue
        if ts - h_ts > window:
            continue
        if fingerprint(h_text) != want:
            continue
        seen.add(str(h_channel))
    seen.add(str(channel))
    if len(seen) < min_channels:
        return None
    width = len(seen)
    score = round(min(1.0, width / (min_channels * 2)), 3)
    return CampaignSignal(
        campaign_id=campaign_id(text, ts),
        fingerprint=want,
        channels=width,
        score=score,
        reasons=(f"{width} distinct channels pasted the same pitch inside {window // 3600}h",),
    )


__all__ = ["CampaignSignal", "campaign_id", "fingerprint", "score_campaign", "to_epoch"]
