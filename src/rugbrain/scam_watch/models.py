"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Records shared by the scam-watch pipeline: watchlist, video metadata, signals,
evidence rows and the article brief handed to the content pipeline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SignalCode = Literal[
    "contract_address",
    "guarantee_language",
    "presale_language",
    "urgency_language",
    "missing_disclosure",
    "new_token",
    "holder_concentration",
    "honeypot_flags",
    "deployer_linkage",
    "bad_actor_funding",
]
Cadence = Literal["daily", "weekly", "biweekly", "monthly", "ad_hoc"]
EvidenceKind = Literal["onchain", "platform", "video_metadata", "third_party_report"]


class StrictModel(BaseModel):
    """Closed schema: an unexpected key is a bug, not a field to ignore."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RiskEvent(StrictModel):
    at: datetime
    kind: Literal["promo", "unverified_claim", "retraction"] = "promo"
    reference: str
    summary: str


class ChannelRecord(StrictModel):
    name: str
    handle: str
    channel_url: str
    category: str = "unknown"
    cadence: Cadence = "weekly"
    subs: int | None = None
    subs_source: str | None = None
    subs_checked_at: datetime | None = None
    last_checked: datetime | None = None
    risk_history: tuple[RiskEvent, ...] = ()
    notes: str | None = None

    @field_validator("handle")
    @classmethod
    def _handle_shape(cls, value: str) -> str:
        if not value.startswith("@") or len(value) < 2:
            raise ValueError("handle must start with '@'")
        return value

    @field_validator("channel_url")
    @classmethod
    def _youtube_url(cls, value: str) -> str:
        if not value.startswith(("https://www.youtube.com/", "https://youtube.com/")):
            raise ValueError("channel_url must be a youtube.com URL")
        return value


class VideoMeta(StrictModel):
    video_id: str
    title: str
    description: str = ""
    pinned_comment: str = ""
    published_at: datetime
    has_paid_promo_label: bool | None = None
    token_launched_at: datetime | None = None
    holder_top10_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    honeypot: bool | None = None
    buy_tax_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    sell_tax_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    deployer_linked_to: tuple[str, ...] = ()


class Signal(StrictModel):
    code: SignalCode
    weight: float = Field(ge=0.0, le=1.0)
    detail: str
    address: str | None = None


class EvidenceItem(StrictModel):
    kind: EvidenceKind
    url: str
    source: str
    captured_at: datetime
    claim: str
    confidence: float = Field(ge=0.0, le=1.0)


class Verdict(StrictModel):
    publishable: bool
    confidence: float = Field(ge=0.0, le=1.0)
    independent_sources: int
    reasons: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()


class TimelineEntry(StrictModel):
    at: datetime
    event: str
    evidence_url: str | None = None


class ArticleBrief(StrictModel):
    slug: str
    headline: str
    dek: str
    channel: str
    handle: str
    video_id: str
    video_url: str
    status: Literal["draft"] = "draft"
    framing: Literal["alleged"] = "alleged"
    confidence: float = Field(ge=0.0, le=1.0)
    signals: tuple[Signal, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    timeline: tuple[TimelineEntry, ...] = ()
    legal_notes: tuple[str, ...] = ()
    prior_reports: int = 0
    repeat_offender: bool = False
    review: str = "[REVIEW]"
    generated_at: datetime
