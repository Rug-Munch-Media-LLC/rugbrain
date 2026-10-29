"""A brief is a draft with alleged framing and legal notes, or it does not exist."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rugbrain.scam_watch.brief import InsufficientEvidence, build_brief
from rugbrain.scam_watch.models import (
    ChannelRecord,
    EvidenceItem,
    Signal,
    VideoMeta,
)
from rugbrain.scam_watch.verify import assess

FIXTURE = Path(__file__).parent / "fixtures" / "scam_watch" / "promoted_token.json"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
CHANNEL = ChannelRecord(
    name="Example Crypto Show",
    handle="@ExampleCryptoShow",
    channel_url="https://www.youtube.com/@ExampleCryptoShow",
)
VIDEO = VideoMeta(
    video_id="dQw4w9WgXcQ",
    title="Weekly update",
    published_at=NOW - timedelta(hours=2),
)
SIGNALS = (
    Signal(
        code="contract_address",
        weight=0.35,
        detail="evm address in title",
        address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    ),
    Signal(code="missing_disclosure", weight=0.25, detail="no disclosure"),
)
EVIDENCE = (
    EvidenceItem(
        kind="onchain",
        url="https://rugmunch.io/token/0xa0b8",
        source="rugmunch-data",
        captured_at=NOW - timedelta(hours=1),
        claim="holders",
        confidence=0.9,
    ),
    EvidenceItem(
        kind="platform",
        url="https://solscan.io/token/0xa0b8",
        source="solscan",
        captured_at=NOW - timedelta(hours=1),
        claim="listing",
        confidence=0.85,
    ),
)


def test_brief_carries_alleged_framing_and_legal_notes():
    verdict = assess(SIGNALS, EVIDENCE, NOW)
    brief = build_brief(CHANNEL, VIDEO, SIGNALS, verdict, EVIDENCE, NOW)
    assert brief.status == "draft"
    assert brief.framing == "alleged"
    assert brief.review == "[REVIEW]"
    assert brief.dek.startswith("Alleged:")
    assert len(brief.legal_notes) >= 5
    assert any("Right of reply" in note for note in brief.legal_notes)
    assert len(brief.headline) <= 70
    assert brief.slug.startswith("yt-examplecryptoshow-")


def test_timeline_is_chronological_and_links_evidence():
    verdict = assess(SIGNALS, EVIDENCE, NOW)
    brief = build_brief(CHANNEL, VIDEO, SIGNALS, verdict, EVIDENCE, NOW)
    stamps = [entry.at for entry in brief.timeline]
    assert stamps == sorted(stamps)
    assert {entry.evidence_url for entry in brief.timeline} >= {
        "https://rugmunch.io/token/0xa0b8",
        "https://solscan.io/token/0xa0b8",
    }


def test_insufficient_verdict_refuses_to_build_a_brief():
    verdict = assess(SIGNALS, EVIDENCE[:1], NOW)
    assert verdict.publishable is False
    with pytest.raises(InsufficientEvidence) as caught:
        build_brief(CHANNEL, VIDEO, SIGNALS, verdict, EVIDENCE[:1], NOW)
    assert "fewer_than_two_independent_sources" in caught.value.verdict.failures


def test_fixture_produces_a_complete_brief():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    channel = ChannelRecord.model_validate(payload["channel"])
    video = VideoMeta.model_validate(payload["video"])
    evidence = tuple(EvidenceItem.model_validate(row) for row in payload["evidence"])
    from rugbrain.scam_watch.signals import detect_signals

    signals = detect_signals(video)
    verdict = assess(signals, evidence, NOW)
    assert verdict.publishable is True
    brief = build_brief(channel, video, signals, verdict, evidence, NOW)
    assert brief.evidence == tuple(sorted(evidence, key=lambda item: item.captured_at))
    assert brief.confidence >= 0.6
    assert {"contract_address", "missing_disclosure", "new_token"} <= {
        signal.code for signal in brief.signals
    }
