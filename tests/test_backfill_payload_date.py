"""Tests for the crypto payload date/chain backfill normalisers.

Read-only: no Qdrant connection, no writes. These cover the two format hazards
found in the live corpus — unpadded ``2021-6-20`` and ``MM/DD/YYYY`` — because
both break Qdrant's RFC 3339 datetime range filter or sort lexicographically.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.backfill_payload_date import build_updates, derive_date
from scripts.qdrant_date_lib import canonical_chain, canonical_date, dig


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2023-08-13", "2023-08-13T00:00:00Z"),
        ("2021-6-20", "2021-06-20T00:00:00Z"),
        ("2021-06-20", "2021-06-20T00:00:00Z"),
        ("08/13/2023", "2023-08-13T00:00:00Z"),
        ("01/04/2022", "2022-01-04T00:00:00Z"),
        ("2026-09-26T18:14:01.047541+00:00", "2026-09-26T18:14:01Z"),
        ("2026-09-26T18:14:01Z", "2026-09-26T18:14:01Z"),
        (1700000000, "2023-11-14T22:13:20Z"),
        ("1700000000.0", "2023-11-14T22:13:20Z"),
        ("", ""),
        (None, ""),
        ("not a date", ""),
        ("2023-13-45", ""),
    ],
)
def test_canonical_date(raw: object, expected: str) -> None:
    assert canonical_date(raw) == expected


def test_unpadded_sorts_after_padded_and_both_normalise() -> None:
    """2021-6-20 and 2021-06-20 must produce the SAME canonical value."""
    assert canonical_date("2021-6-20") == canonical_date("2021-06-20")
    assert canonical_date("2021-6-20") < canonical_date("2021-07-01")


def test_candidates_are_tried_in_trust_order() -> None:
    assert canonical_date(None, "", "2024-01-02") == "2024-01-02T00:00:00Z"
    assert canonical_date("bad", "2024-01-02") == "2024-01-02T00:00:00Z"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("base", "base"),
        ("Base", "base"),
        (" EVM ", "evm"),
        ("", ""),
        ("unknown", ""),
        ("multi", ""),
        ("ticker", "ticker"),
        (None, ""),
    ],
)
def test_canonical_chain(raw: object, expected: str) -> None:
    assert canonical_chain(raw) == expected


def test_dig_tolerates_missing_hops() -> None:
    payload = {"metadata": {"date": "2024-01-01"}}
    assert dig(payload, "metadata.date") == "2024-01-01"
    assert dig(payload, "metadata.absent.deep") is None
    assert dig({}, "a.b.c") is None


def test_derive_date_prefers_exploit_date_over_metadata_date() -> None:
    payload = {"date": "2024-04-15", "metadata": {"date": "2020-01-01"}}
    assert derive_date("defi_exploits", payload) == "2024-04-15T00:00:00Z"


def test_derive_date_falls_back_to_content_text() -> None:
    payload = {"metadata": {"date": "04/15/2024"}, "content": "post mortem header"}
    assert derive_date("defi_exploits", payload) == "2024-04-15T00:00:00Z"


def test_derive_date_returns_empty_when_undatable() -> None:
    assert derive_date("news_v2", {"content": "no date anywhere"}) == ""


def test_build_updates_is_idempotent_and_skips_no_ops() -> None:
    point = {"id": "a", "payload": {"date": "2024-04-15T00:00:00Z", "chain": "base"}}
    assert build_updates("defi_exploits", [point]) == []


def test_build_updates_writes_only_changed_fields() -> None:
    point = {"id": "a", "payload": {"date": "2024-04-15T00:00:00Z", "chain": "BASE"}}
    updates = build_updates("defi_exploits", [point])
    assert updates == [{"id": "a", "payload": {"chain": "base"}}]


def test_build_updates_normalises_unpadded_date() -> None:
    point = {"id": "a", "payload": {"date": "2021-6-20"}}
    updates = build_updates("defi_exploits", [point])
    assert updates[0]["payload"]["date"] == "2021-06-20T00:00:00Z"


def test_build_updates_writes_nothing_when_there_is_nothing_to_fix() -> None:
    point = {"id": "a", "payload": {"content": "nothing dated here", "chain": "base"}}
    assert build_updates("news_v2", [point]) == []


def test_build_updates_never_writes_an_empty_date() -> None:
    """An undatable point keeps its own chain fix but never gains a date key."""
    point = {"id": "a", "payload": {"content": "nothing dated here", "chain": "BASE"}}
    updates = build_updates("news_v2", [point])
    assert "date" not in updates[0]["payload"]
    assert updates[0]["payload"]["chain"] == "base"
