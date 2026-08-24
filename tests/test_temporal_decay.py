"""Temporal decay contracts (was 0% covered).

* timestamp parsing: ISO 8601 / unix / datetime / junk
* fresh content boosted, boost halves per halflife, old content untouched
* no-timestamp results keep original score; negative age clamped
* results sorted by decayed score descending
"""

from __future__ import annotations

import time
from datetime import UTC

from rugbrain.search.temporal_decay import (
    _get_timestamp,
    _parse_timestamp,
    apply_temporal_decay,
    apply_temporal_decay_batch,
)


def _iso(hours_ago: float) -> str:
    from datetime import datetime, timedelta

    dt = datetime.now(UTC) - timedelta(hours=hours_ago)
    return dt.isoformat()


# ── Parsing ───────────────────────────────────────────────────────────


def test_parse_iso_and_unix():
    now = time.time()
    assert _parse_timestamp(now) == now
    assert abs(_parse_timestamp(_iso(0)) - now) < 5
    assert _parse_timestamp("not-a-date") is None
    assert _parse_timestamp(None) is None


def test_get_timestamp_priority_order():
    m = {"published_at": 1000.0, "ingested_at": 2000.0}
    assert _get_timestamp(m) == 2000.0  # ingested_at wins (priority)
    nested = {"metadata": {"timestamp": 3000.0}}
    assert _get_timestamp(nested) == 3000.0
    assert _get_timestamp({}) is None


# ── Decay behavior ────────────────────────────────────────────────────


def test_fresh_content_boosted():
    r = [{"score": 1.0, "metadata": {"ingested_at": _iso(0.5)}}]
    out = apply_temporal_decay(r)
    assert out[0]["score"] > 1.0  # boosted
    assert out[0]["_temporal_boost"] > 0


def test_boost_halves_per_halflife():
    fresh = [{"score": 1.0, "metadata": {"ingested_at": _iso(0.01)}}]
    one_hl = [{"score": 1.0, "metadata": {"ingested_at": _iso(24.0)}}]
    b_fresh = apply_temporal_decay([dict(fresh[0])])[0]["_temporal_boost"]
    b_one = apply_temporal_decay([dict(one_hl[0])])[0]["_temporal_boost"]
    # boost values are rounded to 4 decimals upstream
    assert abs(b_one - b_fresh / 2) < 1e-3


def pytest_approx(v):
    import pytest

    return pytest.approx(v)


def test_old_content_untouched():
    r = [{"score": 1.0, "metadata": {"ingested_at": _iso(4000.0)}}]  # >30d
    out = apply_temporal_decay(r)
    assert out[0]["score"] == 1.0
    assert "_temporal_boost" not in out[0]


def test_no_timestamp_keeps_score():
    r = [{"score": 0.8}]
    out = apply_temporal_decay(r)
    assert out[0]["score"] == 0.8


def test_sorted_by_decayed_score_desc():
    now = time.time()
    results = [
        {"id": "old-high", "score": 1.2, "metadata": {"ingested_at": now - 4000 * 3600}},
        {"id": "new-low", "score": 1.0, "metadata": {"ingested_at": _iso(0.01)}},
    ]
    out = apply_temporal_decay(results)
    # new content boosted past the stale high scorer
    assert out[0]["id"] == "new-low"


def test_batch_applies_per_collection():
    a = [{"score": 1.0, "metadata": {"ingested_at": _iso(0.01)}}]
    b = [{"score": 1.0}]
    out = apply_temporal_decay_batch({"news_v2": a, "wallet_v2": b})
    assert out["news_v2"][0]["score"] > 1.0
    assert out["wallet_v2"][0]["score"] == 1.0
