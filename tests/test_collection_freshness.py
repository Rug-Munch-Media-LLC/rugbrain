"""Tests for collection freshness — the rules, not just the plumbing.

The four things that must never regress:

  1. A content date is never read as ingest time.
  2. A Qdrant gap for a Neo4j/ClickHouse collection is not reported as stale.
  3. Every collection is MEASURED or UNMEASURABLE-with-a-reason. Never silent.
  4. An absent timestamp yields a gap, never an invented age.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugbrain import collection_freshness as cf
from rugbrain.freshness_classify import FieldClass, classify_field, is_ingest_field
from rugbrain.freshness_report import (
    Measurement,
    OffQdrantStore,
    Probe,
    summarize,
    verdict_for,
)
from rugbrain.freshness_transport import parse_timestamp, payload_leaves

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def days_ago(n: float) -> datetime:
    return NOW - timedelta(days=n)


# --- 1. ingest time is not content date ---------------------------------


def test_ingested_at_is_ingest_and_published_is_content():
    assert classify_field("metadata.ingested_at") is FieldClass.INGEST
    assert classify_field("metadata.published") is FieldClass.CONTENT
    assert classify_field("published") is FieldClass.CONTENT
    assert not is_ingest_field("metadata.published", "news_v2")


def test_due_date_is_agent_stamped_not_ingest():
    """due_date is when a judgement was made, not when the corpus was fed."""
    assert classify_field("metadata.due_date") is FieldClass.AGENT
    assert not is_ingest_field("metadata.due_date", "rag_documents")


def test_ts_is_content_for_every_corpus_including_documents():
    """A document's own clock is never promoted, not even for news.

    An earlier version accepted `ts` as an ingest proxy for document-shaped
    corpora. Measured: `news_v2` flipped between unmeasurable and fresh across
    two runs on a stable collection, and `protocol_design`'s `date` reported 58
    architecture docs as "aging" when it is a revision date. No corpus makes a
    content date into ingest evidence.
    """
    assert classify_field("ts", "known_scams") is FieldClass.CONTENT
    assert classify_field("ts", "news_v2") is FieldClass.CONTENT
    assert classify_field("metadata.ts", "news_v2") is FieldClass.CONTENT
    assert classify_field("date", "protocol_design") is FieldClass.CONTENT
    assert not is_ingest_field("ts", "news_v2")
    assert not is_ingest_field("date", "protocol_design")


def test_ingest_fields_are_the_only_read_ones():
    for field in ("metadata.ingested_at", "ingested_at", "metadata.detected_at", "created_at"):
        assert is_ingest_field(field, "known_scams"), field
        assert classify_field(field, "news_v2") is FieldClass.INGEST


def test_future_content_date_is_refused_not_believed():
    """The measured trap: a 2026-12-08 content date would read as 'fresh'."""
    future = (datetime.now(UTC) + timedelta(days=11)).strftime("%Y-%m-%d")
    assert parse_timestamp(future) is None


def test_implausible_epochs_and_bools_are_refused():
    assert parse_timestamp(True) is None
    assert parse_timestamp(None) is None
    assert parse_timestamp(123) is None
    assert parse_timestamp("nope") is None


def test_valid_timestamps_parse_to_aware_utc():
    got = parse_timestamp("2026-09-27T10:00:00Z")
    assert got == datetime(2026, 9, 27, 10, 0, tzinfo=UTC)
    assert parse_timestamp(1789000000).tzinfo is UTC
    assert parse_timestamp(1789000000000) == parse_timestamp(1789000000)


# --- 2. a qdrant gap is not a fleet gap ---------------------------------


def test_off_qdrant_collection_is_unmeasurable_not_stale():
    probe = cf.probe_collection("wallet_profiles", "http://127.0.0.1:1")
    assert probe.measurement is Measurement.UNMEASURABLE
    assert probe.reason == "off_qdrant"
    assert probe.store is OffQdrantStore.NEO4J
    assert probe.verdict.value == "unmeasurable"


def test_off_qdrant_short_circuits_without_touching_qdrant():
    """An unreachable URL still returns off_qdrant: no read was attempted."""
    probe = cf.probe_collection("wallet_clusters", "http://127.0.0.1:1")
    assert probe.reason == "off_qdrant"
    assert probe.scanned == 0


# --- 3/4. gaps are visible, never invented ------------------------------


def test_probe_error_fails_closed_with_a_reason():
    probe = cf.probe_collection("some_collection", "http://127.0.0.1:1", timeout=1.0)
    assert probe.measurement is Measurement.UNMEASURABLE
    assert probe.reason.startswith("probe_error")
    assert probe.age_days is None


def test_every_probe_carries_a_reason():
    """No probe may be verdict-only: a gap without a reason is the old bug."""
    rows = [
        Probe(
            "a", 10, cf.FreshnessVerdict.UNMEASURABLE, Measurement.UNMEASURABLE, "no_ingest_field"
        ),
        Probe("b", 0, cf.FreshnessVerdict.UNMEASURABLE, Measurement.UNMEASURABLE, "empty"),
        Probe(
            "c", 5, cf.FreshnessVerdict.STALE, Measurement.MEASURED, "ingest_field", days_ago(30)
        ),
    ]
    assert all(r.reason for r in rows)


def test_unmeasurable_never_reports_an_age():
    probe = Probe(
        "x", 100, cf.FreshnessVerdict.UNMEASURABLE, Measurement.UNMEASURABLE, "no_ingest_field"
    )
    assert probe.age_days is None
    assert probe.as_dict()["newest_ingest"] is None


def test_verdict_thresholds_sit_in_the_measured_gap():
    assert verdict_for(days_ago(0), NOW).value == "fresh"
    assert verdict_for(days_ago(4), NOW).value == "fresh"
    assert verdict_for(days_ago(10), NOW).value == "aging"
    assert verdict_for(days_ago(17), NOW).value == "stale"
    assert verdict_for(None, NOW).value == "unmeasurable"


def test_summarize_reports_blind_mass_not_just_counts():
    """81.9% of points blind is the headline; 53 of 96 hides it."""
    rows = [
        Probe(
            "big",
            2_000_000,
            cf.FreshnessVerdict.UNMEASURABLE,
            Measurement.UNMEASURABLE,
            "no_ingest_field",
        ),
        Probe(
            "small",
            100,
            cf.FreshnessVerdict.STALE,
            Measurement.MEASURED,
            "ingest_field",
            days_ago(30),
        ),
    ]
    out = summarize(rows)
    assert out["total"] == 2
    assert out["blind_points"] == 2_000_000
    assert out["blind_share"] == pytest.approx(2_000_000 / 2_000_100)
    assert out["measured"] == 1


# --- payload flattening -------------------------------------------------


def test_payload_leaves_flattens_metadata_and_samples_lists():
    flat = payload_leaves(
        {"metadata": {"ingested_at": "2026-09-27T00:00:00Z", "tags": ["a", "b"]}, "content": "x"}
    )
    assert flat["metadata.ingested_at"] == "2026-09-27T00:00:00Z"
    assert flat["content"] == "x"
    assert "metadata.tags" in flat
