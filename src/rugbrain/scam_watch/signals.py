"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Signal detection over one video. Weights are explicit constants so a brief can
explain its own score; no model, no per-channel tuning. The sum is a triage
score and is capped at 1.0 — it is not a probability of wrongdoing.
"""

from __future__ import annotations

from datetime import timedelta

from rugbrain.scam_watch.addresses import extract_addresses
from rugbrain.scam_watch.lexicon import (
    GUARANTEE_TERMS,
    PRESALE_TERMS,
    PROMO_TERMS,
    URGENCY_TERMS,
    find_terms,
    has_disclosure,
)
from rugbrain.scam_watch.models import Signal, VideoMeta

WEIGHTS = {
    "address_in_title_or_pin": 0.35,
    "address_in_description": 0.20,
    "guarantee_language": 0.20,
    "presale_language": 0.15,
    "urgency_language": 0.05,
    "missing_disclosure": 0.25,
    "new_token": 0.20,
    "holder_concentration": 0.25,
    "honeypot_flags": 0.40,
    "tax_above_10pct": 0.20,
    "deployer_linkage": 0.35,
}

#: A token launched more than this before the video is not "fresh".
NEW_TOKEN_WINDOW = timedelta(hours=72)

#: Top-10 holders above this share is concentrated enough to flag.
CONCENTRATION_THRESHOLD = 0.50

#: Buy or sell tax above this is treated as a transfer trap worth flagging.
TAX_THRESHOLD_PCT = 10.0


def _address_signals(video: VideoMeta) -> list[Signal]:
    fields = (
        ("title", video.title),
        ("pinned_comment", video.pinned_comment),
        ("description", video.description),
    )
    signals: list[Signal] = []
    seen: set[str] = set()
    for field, text in fields:
        for hit in extract_addresses(text, field=field):
            if hit.address in seen:
                continue
            seen.add(hit.address)
            weight = (
                WEIGHTS["address_in_title_or_pin"]
                if field in {"title", "pinned_comment"}
                else WEIGHTS["address_in_description"]
            )
            signals.append(
                Signal(
                    code="contract_address",
                    weight=weight,
                    detail=f"{hit.chain} address in {field.replace('_', ' ')}",
                    address=hit.address,
                )
            )
    return signals


def _language_signals(video: VideoMeta) -> list[Signal]:
    corpus = " ".join((video.title, video.description, video.pinned_comment))
    signals: list[Signal] = []
    for code, terms, weight in (
        ("guarantee_language", GUARANTEE_TERMS, WEIGHTS["guarantee_language"]),
        ("presale_language", PRESALE_TERMS, WEIGHTS["presale_language"]),
        ("urgency_language", URGENCY_TERMS, WEIGHTS["urgency_language"]),
    ):
        found = find_terms(corpus, terms)
        if found:
            signals.append(Signal(code=code, weight=weight, detail=", ".join(found)))
    promo = find_terms(corpus, PROMO_TERMS)
    if promo and not has_disclosure(corpus, video.has_paid_promo_label):
        signals.append(
            Signal(
                code="missing_disclosure",
                weight=WEIGHTS["missing_disclosure"],
                detail=f"promo terms without disclosure: {', '.join(promo[:4])}",
            )
        )
    return signals


def _onchain_signals(video: VideoMeta) -> list[Signal]:
    signals: list[Signal] = []
    if video.token_launched_at is not None:
        age = video.published_at - video.token_launched_at
        if timedelta(0) <= age <= NEW_TOKEN_WINDOW:
            signals.append(
                Signal(
                    code="new_token",
                    weight=WEIGHTS["new_token"],
                    detail=f"token launched {age.total_seconds() / 3600:.1f}h before video",
                )
            )
    if video.holder_top10_pct is not None and video.holder_top10_pct >= CONCENTRATION_THRESHOLD:
        signals.append(
            Signal(
                code="holder_concentration",
                weight=WEIGHTS["holder_concentration"],
                detail=f"top-10 holders {video.holder_top10_pct:.0%} of supply",
            )
        )
    if video.honeypot is True:
        signals.append(
            Signal(code="honeypot_flags", weight=WEIGHTS["honeypot_flags"], detail="honeypot flag")
        )
    taxes = [tax for tax in (video.buy_tax_pct, video.sell_tax_pct) if tax is not None]
    if taxes and max(taxes) > TAX_THRESHOLD_PCT:
        signals.append(
            Signal(
                code="honeypot_flags",
                weight=WEIGHTS["tax_above_10pct"],
                detail=f"transfer tax up to {max(taxes):.1f}%",
            )
        )
    if video.deployer_linked_to:
        signals.append(
            Signal(
                code="deployer_linkage",
                weight=WEIGHTS["deployer_linkage"],
                detail="deployer linked to: " + ", ".join(video.deployer_linked_to),
            )
        )
    return signals


def detect_signals(video: VideoMeta) -> tuple[Signal, ...]:
    """Detect promotion red flags in one video; empty means nothing surfaced."""
    return tuple(_address_signals(video) + _language_signals(video) + _onchain_signals(video))
