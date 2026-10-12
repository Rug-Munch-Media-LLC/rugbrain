"""The footprint must contain REAL broadcast channels only (operator 2026-10-12).

Rejection reasons are pinned here so a loosening of the vetting (which would
sweep groups, user accounts or spam channels into news_v2_8b) fails the build.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rugbrain.ingest_sources import alt_ingest_vet as refresh


def _entity(**kw):
    base = {"broadcast": True, "scam": False, "fake": False, "restricted": False,
            "participants_count": 50_000}
    base.update(kw)
    return SimpleNamespace(**base)


def test_broadcast_channel_accepted():
    assert refresh.vet("coindesk", _entity()) is None


@pytest.mark.parametrize(
    "name,entity,reason",
    [
        ("some_group", _entity(broadcast=False, participants_count=90_000), "not_broadcast"),
        ("spammy", _entity(scam=True), "telegram_flagged"),
        ("rmialpha", _entity(), "our_own"),
        ("tinychannel", _entity(participants_count=12), "under_1k"),
        ("secret_alpha_calls", _entity(), "junk_name"),
    ],
)
def test_rejections(name, entity, reason):
    assert refresh.vet(name, entity) == reason


def test_our_own_publishing_channels_are_never_swept():
    for handle in refresh.OUR_OWN:
        assert refresh.vet(handle, _entity()) == "our_own"
