"""Signal weights are explicit and the sum is a triage score, not a verdict."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rugbrain.scam_watch.models import VideoMeta
from rugbrain.scam_watch.signals import detect_signals

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
EVM = "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def video(**overrides: object) -> VideoMeta:
    payload: dict[str, object] = {
        "video_id": "abc123",
        "title": "Weekly market update",
        "description": "",
        "published_at": NOW,
    }
    payload.update(overrides)
    return VideoMeta.model_validate(payload)


def test_benign_video_produces_no_signals():
    assert detect_signals(video()) == ()


def test_address_language_and_missing_disclosure_stack():
    signals = detect_signals(
        video(
            title=f"GUARANTEED 100x presale {EVM}",
            description="join telegram, link below",
        )
    )
    codes = {signal.code for signal in signals}
    assert {"contract_address", "guarantee_language", "presale_language"} <= codes
    assert "missing_disclosure" in codes
    assert sum(signal.weight for signal in signals) >= 0.75


def test_disclosed_promotion_does_not_raise_missing_disclosure():
    signals = detect_signals(
        video(title="presale is live", description="This video is sponsored by Acme")
    )
    assert "missing_disclosure" not in {signal.code for signal in signals}


def test_paid_promo_label_suppresses_missing_disclosure():
    signals = detect_signals(video(title="presale live", has_paid_promo_label=True))
    assert "missing_disclosure" not in {signal.code for signal in signals}


def test_new_token_only_inside_72_hours():
    fresh = detect_signals(video(token_launched_at=NOW - timedelta(hours=24)))
    stale = detect_signals(video(token_launched_at=NOW - timedelta(days=5)))
    assert "new_token" in {signal.code for signal in fresh}
    assert "new_token" not in {signal.code for signal in stale}


def test_onchain_flags_become_signals():
    signals = detect_signals(
        video(
            honeypot=True,
            holder_top10_pct=0.62,
            buy_tax_pct=5.0,
            sell_tax_pct=25.0,
            deployer_linked_to=("wallet:0xdead",),
        )
    )
    codes = [signal.code for signal in signals]
    assert codes.count("honeypot_flags") == 2  # flag + tax
    assert "holder_concentration" in codes
    assert "deployer_linkage" in codes
