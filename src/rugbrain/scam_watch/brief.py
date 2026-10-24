"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Article-brief construction for the content pipeline.

Every brief is a ``draft``, is framed as an ``alleged`` finding, carries the
legal-safety notes below, and is marked ``[REVIEW]`` so a human editor owns the
publication decision. The builder refuses any verdict that did not pass
:mod:`rugbrain.scam_watch.verify`; there is no "force" argument.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from rugbrain.scam_watch.models import (
    ArticleBrief,
    ChannelRecord,
    EvidenceItem,
    Signal,
    TimelineEntry,
    Verdict,
    VideoMeta,
)

LEGAL_NOTES: tuple[str, ...] = (
    "Framing: 'alleged'. The brief asserts no intent, ownership or control; it "
    "reports observable signals and cites each one.",
    "Right of reply: contact the channel and any named project for comment "
    "before publication, and append the response verbatim.",
    "Evidence preservation: archive every URL (archive.org or local snapshot) "
    "and keep the capture timestamps shown in the evidence table.",
    "Single-source rule: no claim may rest on one heuristic; every row maps to "
    "a dated, independent source.",
    "Legal review: defamation and commercial-disclosure law vary by "
    "jurisdiction (US/UK/EU); a lawyer signs off before any named individual.",
    "Corrections: if a row is refuted, publish a dated correction with the "
    "original retained and struck through.",
)

_MAX_HEADLINE = 70


class InsufficientEvidence(RuntimeError):
    """Raised when a verdict did not clear the verification gate."""

    def __init__(self, verdict: Verdict) -> None:
        super().__init__("insufficient evidence: " + ", ".join(verdict.failures))
        self.verdict = verdict


def _short(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}" if len(address) > 12 else address


def _headline(handle: str, signals: Sequence[Signal], prior: int) -> str:
    base = f"{handle}: alleged undisclosed token promotion"
    if any(signal.code == "contract_address" for signal in signals):
        base = f"{handle}: alleged undisclosed token promo"
    if prior:
        base = f"{base} (repeat)"
    return base[:_MAX_HEADLINE]


def _timeline(
    video: VideoMeta, signals: Sequence[Signal], evidence: Sequence[EvidenceItem]
) -> tuple[TimelineEntry, ...]:
    rows = [
        TimelineEntry(
            at=video.published_at,
            event=f"video published: {' '.join(video.title.split())[:80]}",
            evidence_url=f"https://www.youtube.com/watch?v={video.video_id}",
        )
    ]
    if video.token_launched_at is not None:
        rows.append(TimelineEntry(at=video.token_launched_at, event="token contract launched"))
    for signal in signals:
        if signal.code in {"deployer_linkage", "honeypot_flags", "holder_concentration"}:
            rows.append(TimelineEntry(at=video.published_at, event=signal.detail))
    rows.extend(
        TimelineEntry(
            at=item.captured_at,
            event=f"{item.kind} evidence captured ({item.source})",
            evidence_url=item.url,
        )
        for item in evidence
    )
    return tuple(sorted(rows, key=lambda row: row.at))


def build_brief(
    channel: ChannelRecord,
    video: VideoMeta,
    signals: Sequence[Signal],
    verdict: Verdict,
    evidence: Sequence[EvidenceItem],
    now: datetime | None = None,
) -> ArticleBrief:
    """Build a pipeline-ready draft, or raise :class:`InsufficientEvidence`."""
    if not verdict.publishable:
        raise InsufficientEvidence(verdict)

    primary = next((signal.address for signal in signals if signal.address), None)
    label = _short(primary) if primary else "an unnamed token"
    sources = sorted({item.source for item in evidence})
    dek = (
        f"Alleged: {channel.name} promoted {label} in a video published "
        f"{video.published_at:%B %d, %Y} without a visible paid-promotion disclosure. "
        f"{verdict.independent_sources} independent sources ({', '.join(sources)}) support "
        "the signals below. This is an allegation, not a finding of wrongdoing; the "
        "channel had not been contacted when this draft was generated."
    )
    slug = re.sub(r"[^a-z0-9]+", "-", f"yt-{channel.handle}-{video.video_id}".lower()).strip("-")
    return ArticleBrief(
        slug=slug,
        headline=_headline(channel.handle, signals, 0),
        dek=dek,
        channel=channel.name,
        handle=channel.handle,
        video_id=video.video_id,
        video_url=f"https://www.youtube.com/watch?v={video.video_id}",
        confidence=verdict.confidence,
        signals=tuple(signals),
        evidence=tuple(sorted(evidence, key=lambda item: item.captured_at)),
        timeline=_timeline(video, signals, evidence),
        legal_notes=LEGAL_NOTES,
        generated_at=now or datetime.now(UTC),
    )
