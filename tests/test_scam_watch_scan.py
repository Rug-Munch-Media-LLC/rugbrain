"""The scan loop is bounded, throttled, fail-closed and never publishes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugbrain.scam_watch.models import ChannelRecord, EvidenceItem, VideoMeta
from rugbrain.scam_watch.scan import MAX_CHANNELS_PER_RUN, MIN_SECONDS_BETWEEN_FETCHES, run_scan

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
EVM = "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def channel(index: int, last_checked: datetime | None = None) -> ChannelRecord:
    return ChannelRecord(
        name=f"Channel {index}",
        handle=f"@chan{index}",
        channel_url=f"https://www.youtube.com/@chan{index}",
        cadence="weekly",
        last_checked=last_checked,
    )


def scam_video(video_id: str = "v1") -> VideoMeta:
    return VideoMeta(
        video_id=video_id,
        title=f"GUARANTEED 100x presale {EVM}",
        description="join telegram, link below",
        published_at=NOW - timedelta(hours=2),
        has_paid_promo_label=False,
        token_launched_at=NOW - timedelta(hours=12),
    )


async def no_evidence(address: str, chain: str) -> tuple[EvidenceItem, ...]:
    return (
        EvidenceItem(
            kind="onchain",
            url=f"https://rugmunch.io/token/{address}",
            source="rugmunch-data",
            captured_at=NOW - timedelta(hours=1),
            claim="holder concentration",
            confidence=0.9,
        ),
        EvidenceItem(
            kind="platform",
            url=f"https://solscan.io/token/{address}",
            source="solscan",
            captured_at=NOW - timedelta(hours=1),
            claim="contract listed",
            confidence=0.85,
        ),
    )


async def test_limit_is_hard_capped():
    async def fetch(ch: ChannelRecord) -> tuple[VideoMeta, ...]:
        return ()

    with pytest.raises(ValueError, match="limit"):
        await run_scan([channel(1)], fetch, no_evidence, limit=99)
    assert MAX_CHANNELS_PER_RUN == 5


async def test_only_due_channels_are_checked_and_throttled():
    delays: list[float] = []

    async def sleeper(seconds: float) -> None:
        delays.append(seconds)

    async def fetch(ch: ChannelRecord) -> tuple[VideoMeta, ...]:
        return ()

    due = [channel(index) for index in range(6)]
    report = await run_scan(due, fetch, no_evidence, limit=5, now=NOW, sleep=sleeper)
    assert len(report.checked) == 5
    assert delays == [MIN_SECONDS_BETWEEN_FETCHES] * 4
    assert report.rate_limited_seconds == MIN_SECONDS_BETWEEN_FETCHES * 4


async def test_recently_checked_channel_is_skipped():
    async def fetch(ch: ChannelRecord) -> tuple[VideoMeta, ...]:
        raise AssertionError("must not fetch")

    recent = channel(1, last_checked=NOW - timedelta(hours=2))
    report = await run_scan([recent], fetch, no_evidence, now=NOW)
    assert report.checked == ()


async def test_verified_video_yields_one_draft_brief():
    async def fetch(ch: ChannelRecord) -> tuple[VideoMeta, ...]:
        return (scam_video(),)

    report = await run_scan([channel(1)], fetch, no_evidence, now=NOW)
    assert report.errors == ()
    assert len(report.briefs) == 1
    brief = report.briefs[0]
    assert brief.status == "draft" and brief.framing == "alleged"
    assert brief.handle == "@chan1"
    assert report.verdicts[0].publishable is True


async def test_provider_error_fails_closed_and_is_recorded():
    async def fetch(ch: ChannelRecord) -> tuple[VideoMeta, ...]:
        return (scam_video(),)

    async def broken(address: str, chain: str) -> tuple[EvidenceItem, ...]:
        raise RuntimeError("rugbrain_http_500")

    report = await run_scan([channel(1)], fetch, broken, now=NOW)
    assert report.briefs == ()
    assert report.skipped == ("v1",)
    assert report.errors[0].stage == "evidence"
    assert "rugbrain_http_500" in report.errors[0].error
    assert report.verdicts[0].publishable is False


async def test_channel_fetch_failure_does_not_abort_the_run():
    async def fetch(ch: ChannelRecord) -> tuple[VideoMeta, ...]:
        if ch.handle == "@chan1":
            raise TimeoutError("yt timeout")
        return (scam_video(),)

    report = await run_scan([channel(1), channel(2)], fetch, no_evidence, now=NOW)
    assert report.errors[0].stage == "videos"
    assert report.checked == ("@chan2",)
    assert len(report.briefs) == 1


async def test_low_signal_video_is_skipped_without_evidence_fetch():
    async def fetch(ch: ChannelRecord) -> tuple[VideoMeta, ...]:
        return (VideoMeta(video_id="v9", title="Market update", published_at=NOW),)

    async def must_not_call(address: str, chain: str) -> tuple[EvidenceItem, ...]:
        raise AssertionError("evidence fetch must not run")

    report = await run_scan([channel(1)], fetch, must_not_call, now=NOW)
    assert report.skipped == ("v9",)
    assert report.briefs == ()
