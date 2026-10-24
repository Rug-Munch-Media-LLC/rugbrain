"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Bounded scan loop. Hard caps, injected fetchers, and a sleeper that is a
parameter — so tests exercise the throttle instead of mocking it out.

No login, no private endpoints: callers pass public-data fetchers (yt-dlp /
watch pages / API clients) that respect the platform's terms. A failing channel
is recorded and skipped; one broken fetch never aborts a run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from rugbrain.scam_watch.brief import build_brief
from rugbrain.scam_watch.dedupe import dedupe_brief
from rugbrain.scam_watch.models import (
    ArticleBrief,
    ChannelRecord,
    EvidenceItem,
    StrictModel,
    Verdict,
    VideoMeta,
)
from rugbrain.scam_watch.signals import detect_signals
from rugbrain.scam_watch.verify import MIN_SIGNAL_SCORE, assess

MAX_CHANNELS_PER_RUN = 5
MIN_SECONDS_BETWEEN_FETCHES = 6.0

_CADENCE_HOURS = {
    "daily": 20.0,
    "weekly": 150.0,
    "biweekly": 320.0,
    "monthly": 700.0,
    "ad_hoc": 0.0,
}

FetchVideos = Callable[[ChannelRecord], Awaitable[Sequence[VideoMeta]]]
FetchEvidence = Callable[[str, str], Awaitable[Sequence[EvidenceItem]]]
Sleeper = Callable[[float], Awaitable[None]]


class ScanError(StrictModel):
    channel: str
    stage: str
    error: str


class ScanReport(StrictModel):
    checked: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    briefs: tuple[ArticleBrief, ...] = ()
    errors: tuple[ScanError, ...] = ()
    rate_limited_seconds: float = 0.0
    verdicts: tuple[Verdict, ...] = ()


def _due(channels: Sequence[ChannelRecord], now: datetime, limit: int) -> list[ChannelRecord]:
    def overdue(channel: ChannelRecord) -> bool:
        if channel.last_checked is None:
            return True
        hours = (now - channel.last_checked).total_seconds() / 3600
        return hours >= _CADENCE_HOURS[channel.cadence]

    pool = [channel for channel in channels if overdue(channel)]
    pool.sort(key=lambda channel: channel.last_checked or datetime.min.replace(tzinfo=UTC))
    return pool[:limit]


async def run_scan(
    channels: Sequence[ChannelRecord],
    fetch_videos: FetchVideos,
    fetch_evidence: FetchEvidence,
    existing_briefs: Sequence[ArticleBrief] = (),
    limit: int = MAX_CHANNELS_PER_RUN,
    now: datetime | None = None,
    sleep: Sleeper = asyncio.sleep,
) -> ScanReport:
    """Check at most ``limit`` due channels and return drafts, never a publish."""
    if not 1 <= limit <= MAX_CHANNELS_PER_RUN:
        raise ValueError(f"limit must be between 1 and {MAX_CHANNELS_PER_RUN}")

    moment = now or datetime.now(UTC)
    due = _due(channels, moment, limit)
    checked: list[str] = []
    skipped: list[str] = []
    errors: list[ScanError] = []
    verdicts: list[Verdict] = []
    produced: list[ArticleBrief] = []
    pool = list(existing_briefs)
    throttled = 0.0

    for index, channel in enumerate(due):
        if index:
            await sleep(MIN_SECONDS_BETWEEN_FETCHES)
            throttled += MIN_SECONDS_BETWEEN_FETCHES
        try:
            videos = await fetch_videos(channel)
        except Exception as exc:
            errors.append(ScanError(channel=channel.handle, stage="videos", error=str(exc)[:200]))
            continue
        checked.append(channel.handle)

        for video in videos:
            signals = detect_signals(video)
            if sum(signal.weight for signal in signals) < MIN_SIGNAL_SCORE:
                skipped.append(video.video_id)
                continue
            evidence: list[EvidenceItem] = []
            for address in dict.fromkeys(s.address for s in signals if s.address):
                chain = "evm" if address.startswith("0x") else "solana"
                try:
                    evidence.extend(await fetch_evidence(address, chain))
                except Exception as exc:
                    errors.append(
                        ScanError(channel=channel.handle, stage="evidence", error=str(exc)[:200])
                    )
            verdict = assess(signals, evidence, moment)
            verdicts.append(verdict)
            if not verdict.publishable:
                skipped.append(video.video_id)
                continue
            brief = build_brief(channel, video, signals, verdict, evidence, moment)
            kept = dedupe_brief(pool, brief)
            if kept is None:
                skipped.append(video.video_id)
                continue
            pool.append(kept)
            produced.append(kept)

    return ScanReport(
        checked=tuple(checked),
        skipped=tuple(skipped),
        briefs=tuple(produced),
        errors=tuple(errors),
        rate_limited_seconds=throttled,
        verdicts=tuple(verdicts),
    )
