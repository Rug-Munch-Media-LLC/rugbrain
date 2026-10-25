"""Dedupe: same video once, same channel repeated promotion marked, not republished."""

from __future__ import annotations

from datetime import UTC, datetime

from rugbrain.scam_watch.dedupe import dedupe_brief
from rugbrain.scam_watch.models import ArticleBrief, Signal

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def brief(video_id: str, handle: str = "@chan", address: str | None = "0xabc") -> ArticleBrief:
    signals = (
        (Signal(code="contract_address", weight=0.35, detail="address", address=address),)
        if address
        else ()
    )
    return ArticleBrief(
        slug=f"yt-{handle}-{video_id}",
        headline=f"{handle}: alleged undisclosed token promotion",
        dek="Alleged.",
        channel=handle.lstrip("@"),
        handle=handle,
        video_id=video_id,
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        confidence=0.9,
        signals=signals,
        generated_at=NOW,
    )


def test_same_video_is_never_briefed_twice():
    assert dedupe_brief([brief("v1")], brief("v1")) is None


def test_same_channel_second_video_is_marked_repeat():
    kept = dedupe_brief([brief("v1")], brief("v2", address="0xdef"))
    assert kept is not None
    assert kept.repeat_offender is True
    assert kept.prior_reports == 1


def test_same_token_second_video_by_same_channel_is_repeat():
    kept = dedupe_brief([brief("v1")], brief("v2"))
    assert kept is not None and kept.repeat_offender is True


def test_different_channel_is_not_a_repeat():
    kept = dedupe_brief([brief("v1", handle="@a")], brief("v2", handle="@b"))
    assert kept is not None and kept.repeat_offender is False


def test_case_insensitive_handle_match():
    kept = dedupe_brief([brief("v1", handle="@Chan")], brief("v2", handle="@chan"))
    assert kept is not None and kept.prior_reports == 1
