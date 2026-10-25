"""Causal shill-campaign clustering: burst detection + look-ahead protection."""
from __future__ import annotations

from rugbrain.ingest_sources.shill_campaign import campaign_id, score_campaign

PITCH = "100x gem guaranteed, buy now before it moons, contract 0x" + "a" * 40


def _iso(hour: int, day: int = 1) -> str:
    return f"2026-10-{day:02d}T{hour:02d}:00:00+00:00"


def test_burst_across_three_channels_is_a_campaign():
    hist = [("-1001", PITCH, _iso(1)), ("-1002", PITCH, _iso(2))]
    sig = score_campaign("-1003", PITCH, _iso(3), hist)
    assert sig is not None and sig.channels == 3 and 0 < sig.score <= 1
    assert sig.campaign_id == campaign_id(PITCH, _iso(3))


def test_organic_single_channel_is_not_a_campaign():
    hist = [("-1001", "gm frens, market looks choppy today", _iso(1))]
    assert score_campaign("-1001", "gm frens, market looks choppy today", _iso(2), hist) is None


def test_look_ahead_is_blocked():
    """Future messages MUST NOT influence a decision taken earlier."""
    past = [("-1001", PITCH, _iso(1)), ("-1002", PITCH, _iso(2))]
    future = [("-1003", PITCH, _iso(9)), ("-1004", PITCH, _iso(10))]
    early = score_campaign("-1002", PITCH, _iso(2), past + future)
    later = score_campaign("-1002", PITCH, _iso(2), past)
    assert early == later, "future posts leaked into an earlier decision"


def test_window_expiry_drops_stale_posts():
    hist = [("-1001", PITCH, _iso(1, day=1)), ("-1002", PITCH, _iso(1, day=2))]
    assert score_campaign("-1003", PITCH, _iso(3, day=3), hist) is None


def test_campaign_id_is_stable_and_day_bucketed():
    assert campaign_id(PITCH, _iso(1)) == campaign_id(PITCH, _iso(23))
    assert campaign_id(PITCH, _iso(1)) != campaign_id(PITCH, _iso(1, day=2))
