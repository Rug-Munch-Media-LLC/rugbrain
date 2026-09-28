"""Tests for the ``rug_events`` extractors, against REAL rows from the corpus.

The fixtures in ``tests/fixtures/rug_events_source_rows.json`` were copied
verbatim out of the live ``defi_exploits`` and ``exploits`` collections on
2026-09-28 — including the awkward one: the "Total removed liquidity:
2691364.08" row that carries ``date: ""`` and is exactly the prose trap this
collection exists to escape.

No synthetic row is asserted on. If the real data changes shape, these fail,
which is the intent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rugbrain.rug_events.build_events import dedupe, project_point
from rugbrain.rug_events.deployers import apply_prior_counts, prior_counts, serial_ruggers
from rugbrain.rug_events.schema import validate
from rugbrain.rug_events.solrpds import (
    CONFIDENCE_CODE_BLOCK,
    CONFIDENCE_METADATA,
    build_event,
    parse_code_block,
)

FIXTURES = Path(__file__).parent / "fixtures" / "rug_events_source_rows.json"
_ROWS = json.loads(FIXTURES.read_text(encoding="utf-8"))
TYPED = _ROWS["defi_exploits_solrpds"]
FLAT = _ROWS["exploits_rug"]


# ── the flat (exploits) shape ───────────────────────────────────────────


def test_parse_code_block_normalizes_prose_labels_to_field_names() -> None:
    parsed = parse_code_block(FLAT[0]["payload"]["code_block"])
    assert parsed["year"] == "2022"
    assert parsed["total_added_liquidity"] == "883943.52"
    assert parsed["total_removed_liquidity"] == "2691364.08"
    assert parsed["inactivity_status"] == "Inactive"
    # "Solana rug-pull token" is prose; it maps onto the real field name.
    assert parsed["token_address"] == "6qQnzsoH89TWZirgZS9AJN3NrxS7Y4K7oNt5N93E6QDR"
    # An unmapped label is kept under its own name, never dropped.
    assert parsed["Add/remove ratio"] == "0.328436991"


def test_parse_code_block_tolerates_garbage() -> None:
    assert parse_code_block("") == {}
    assert parse_code_block("no colon here\n\n:::") == {}


def test_build_event_from_code_block_only() -> None:
    """The undated prose row becomes a dated, queryable event."""
    event = build_event(FLAT[0]["payload"], FLAT[0]["id"])
    assert event is not None
    assert event["token_address"] == "6qQnzsoH89TWZirgZS9AJN3NrxS7Y4K7oNt5N93E6QDR"
    assert event["rug_ts"] == "2022-09-08 23:16:56.000"
    assert event["liq_added_usd"] == pytest.approx(883943.52)
    assert event["liq_removed_usd"] == pytest.approx(2691364.08)
    assert event["confidence"] == CONFIDENCE_CODE_BLOCK
    assert not validate(event)


# ── the typed (defi_exploits) shape ────────────────────────────────────


def test_build_event_from_typed_metadata() -> None:
    event = build_event(TYPED[0]["payload"], TYPED[0]["id"])
    assert event is not None
    assert event["chain"] == "Solana"
    assert event["confidence"] == CONFIDENCE_METADATA
    assert event["liq_added_usd"] > 0
    assert not validate(event)


def test_rug_ratio_is_removed_over_added_not_the_source_ratio() -> None:
    """Source ``add_to_remove_ratio`` is added/removed; we store the inverse.

    A rug removes MORE than was added, so our ratio must exceed 1.0 exactly
    when the source ratio is below 1.0. Copying the source field would have
    inverted the single most important number in the collection.
    """
    event = build_event(FLAT[0]["payload"], FLAT[0]["id"])
    assert event is not None
    # 2691364.08 removed / 883943.52 added -> ~3.04
    assert event["rug_ratio"] == pytest.approx(3.0445, rel=1e-3)
    # The raw source field says 0.328 (added/removed) — the opposite direction.
    assert parse_code_block(FLAT[0]["payload"]["code_block"])["Add/remove ratio"] == "0.328436991"


def test_year_from_prose_is_an_int_in_the_event_id() -> None:
    event = build_event(FLAT[0]["payload"], FLAT[0]["id"])
    assert event is not None
    assert event["year"] == "2022"  # stays a string, as the source wrote it


def test_build_event_rejects_non_rug_point() -> None:
    assert build_event({"metadata": {}, "code_block": "reentrancy in vault"}, "x") is None
    assert build_event({}, "x") is None


def test_event_id_is_token_plus_year() -> None:
    event = build_event(FLAT[0]["payload"], FLAT[0]["id"])
    assert event is not None
    assert event["event_id"] == "deflabx_solrpds:6qQnzsoH89TWZirgZS9AJN3NrxS7Y4K7oNt5N93E6QDR:2022"


# ── projection + dedupe ─────────────────────────────────────────────────


def test_project_point_skips_already_projected_rows() -> None:
    already = {"collection": "rug_events", "token_address": "abc"}
    assert project_point({"id": "1", "payload": already}) is None


def test_dedupe_keeps_the_higher_confidence_duplicate() -> None:
    low = {"event_id": "e1", "confidence": CONFIDENCE_CODE_BLOCK, "token_address": "t"}
    high = {"event_id": "e1", "confidence": CONFIDENCE_METADATA, "token_address": "t"}
    out = dedupe([low, high])
    assert len(out) == 1
    assert out[0]["confidence"] == CONFIDENCE_METADATA


def test_dedupe_drops_rows_with_no_identity() -> None:
    assert dedupe([{"confidence": 1.0}]) == []


# ── deployer_prior_rugs ─────────────────────────────────────────────────


def _ev(event_id: str, deployer: str, rug_ts: str) -> dict[str, object]:
    return {"event_id": event_id, "deployer": deployer, "rug_ts": rug_ts}


def test_first_rug_has_zero_priors() -> None:
    counts = prior_counts([_ev("a", "D", "2022-01-01"), _ev("b", "D", "2022-06-01")])
    assert counts == {"a": 0, "b": 1}


def test_deployers_are_counted_independently() -> None:
    counts = prior_counts(
        [_ev("a", "D", "2022-01-01"), _ev("b", "D", "2022-06-01"), _ev("c", "E", "2022-03-01")]
    )
    assert counts == {"a": 0, "b": 1, "c": 0}


def test_unattributed_events_stay_zero_and_are_flagged() -> None:
    events = apply_prior_counts([{"event_id": "x", "deployer": "", "rug_ts": "2022-01-01"}])
    assert events[0]["deployer_prior_rugs"] == 0
    assert events[0]["deployer_attributed"] is False


def test_same_timestamp_orders_deterministically_by_event_id() -> None:
    events = apply_prior_counts(
        [_ev("b", "D", "2022-01-01"), _ev("a", "D", "2022-01-01"), _ev("c", "D", "2022-01-01")]
    )
    got = {e["event_id"]: e["deployer_prior_rugs"] for e in events}
    assert got == {"a": 0, "b": 1, "c": 2}


def test_serial_ruggers_reports_repeat_offenders() -> None:
    events = [_ev("a", "D", "2022-01-01"), _ev("b", "D", "2022-06-01"), _ev("c", "E", "2022-03-01")]
    assert serial_ruggers(events) == {"D": 2}
