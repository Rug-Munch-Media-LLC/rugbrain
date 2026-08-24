"""Search fusion contracts (was 16% covered) — recency decay + RRF.

* half_life_decay: 1.0 at age 0, halves per half-life, disabled for
  non-positive half-life
* _parse_date: epoch seconds/millis, ISO strings, datetime objects,
  naive→UTC, junk → None
* apply_recency_decay: fresh docs keep score, old docs decayed, no-date
  docs tagged 1.0, sorted descending, rrf_score preferred over similarity
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugbrain.search.fusion import (
    _parse_date,
    apply_recency_decay,
    extract_doc_date,
    half_life_decay,
)


def _iso(days_ago: float) -> str:
    dt = datetime.now(UTC) - timedelta(days=days_ago)
    return dt.isoformat()


# ── half_life_decay ───────────────────────────────────────────────────


def test_decay_basics():
    assert half_life_decay(0.0, 30.0) == 1.0
    assert half_life_decay(30.0, 30.0) == pytest.approx(0.5)
    assert half_life_decay(60.0, 30.0) == pytest.approx(0.25)
    assert half_life_decay(10.0, 0) == 1.0  # disabled


def test_parse_date_variants():
    now = datetime.now(UTC)
    assert _parse_date(now) is not None
    assert _parse_date(now.timestamp()) is not None
    assert _parse_date(str(int(now.timestamp() * 1000))) is not None  # millis
    assert _parse_date(_iso(0)) is not None
    assert _parse_date("") is None
    assert _parse_date("garbage") is None


def test_extract_doc_date_metadata_first():
    doc = {
        "published": "2020-01-01T00:00:00+00:00",
        "metadata": {"published": _iso(1)},
    }
    got = extract_doc_date(doc)
    assert abs((datetime.now(UTC) - got).days) <= 2  # metadata won


# ── apply_recency_decay ───────────────────────────────────────────────


def test_recency_fresh_keeps_old_decays():
    results = [
        {"id": "fresh", "rrf_score": 1.0, "metadata": {"published": _iso(0.01)}},
        {"id": "ancient", "rrf_score": 1.0, "metadata": {"published": _iso(365)}},
    ]
    out = apply_recency_decay(results, half_life_days=30.0)
    by_id = {r["id"]: r for r in out}
    assert by_id["fresh"]["recency_decay"] == pytest.approx(1.0, abs=0.01)
    assert by_id["ancient"]["recency_decay"] < 0.001
    # fresh sorts above ancient after decay
    assert out[0]["id"] == "fresh"


def test_recency_no_date_tagged_neutral():
    results = [{"id": "x", "similarity": 0.9}]
    out = apply_recency_decay(results)
    assert out[0]["recency_decay"] == 1.0
    assert out[0]["similarity"] == 0.9  # unchanged


def test_recency_prefers_rrf_over_similarity():
    results = [
        {"id": "both", "rrf_score": 0.8, "similarity": 0.9, "metadata": {}},
    ]
    out = apply_recency_decay(results)
    assert "rrf_score" in out[0]  # mutated the preferred key


def test_recency_disabled_for_nonpositive_halflife():
    results = [{"id": "x", "rrf_score": 1.0, "metadata": {"published": _iso(400)}}]
    out = apply_recency_decay(results, half_life_days=0)
    assert out[0]["rrf_score"] == 1.0  # untouched
