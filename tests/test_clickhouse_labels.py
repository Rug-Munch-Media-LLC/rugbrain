"""Unit tests for the ClickHouse address-label module.

Pure/offline by construction: every test uses a fake client or the pure mapping
helpers. The live lanes are exercised by scripts/export_address_labels.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rugbrain.clickhouse_labels import (
    SCAN_COLUMNS,
    AddressRisk,
    insert_rows,
    label_counts,
    resolve_address,
    scan_row,
    scan_row_id,
)
from rugbrain.clickhouse_store import ensure_table, qualified

PAYLOAD = {
    "kind": "scan_result",
    "doc_id": "scan:base:base_0x05b9686167a91c65cd5890036ec61d8c9dd74fd2:2026-09-23T21:23:05.144611+00:00",
    "address": "base_0x05b9686167a91c65cd5890036ec61d8c9dd74fd2",
    "chain": "base",
    "symbol": "",
    "name": "",
    "risk_score": 65,
    "safety_score": 35,
    "risk_level": "high",
    "risk_flags": ["SENTINEL_HHI_CRITICAL", "SENTINEL_LP_LOCK"],
    "scanned_at": "2026-09-23T21:23:05.144611+00:00",
}
ROW = [
    "addr",
    "base",
    "doc",
    65.0,
    "high",
    35.0,
    ["FLAG"],
    "",
    "",
    "ts",
    "scanner",
    "qdrant:scan_intel",
]


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self.result_rows = rows


class FakeClient:
    """Minimal clickhouse_connect stand-in: records calls, returns canned rows."""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.commands: list[str] = []
        self.inserts: list[tuple[str, list[Any]]] = []
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def command(self, sql: str, *_a: Any, **_k: Any) -> str:
        self.commands.append(sql)
        return ""

    def insert(self, table: str, rows: Any, column_names: Any = None, **_k: Any) -> None:
        self.inserts.append((table, list(rows)))
        if column_names is not None:
            assert list(column_names) == list(SCAN_COLUMNS)
            assert all(isinstance(r, list) for r in rows), "driver requires positional rows"

    def query(self, sql: str, parameters: Any = None, **_k: Any) -> FakeResult:
        self.queries.append((sql, dict(parameters or {})))
        return FakeResult(self.rows)


def test_scan_row_maps_every_payload_field():
    row = scan_row("pt-1", PAYLOAD)
    assert row is not None
    assert row["address"] == PAYLOAD["address"]
    assert row["chain_id"] == "base"
    assert (row["risk_score"], row["safety_score"], row["risk_level"]) == (65.0, 35.0, "high")
    assert row["risk_flags"] == PAYLOAD["risk_flags"]
    assert row["qdrant_point_id"] == "pt-1"
    assert row["provenance"] == "qdrant:scan_intel", "origin must be auditable"
    assert row["licence"] and row["source"] == "rugbrain_scanner"


def test_scan_row_id_is_deterministic():
    a = scan_row_id(PAYLOAD["doc_id"])
    assert a == scan_row_id(PAYLOAD["doc_id"]), "re-export must upsert, not duplicate"
    assert len(a) == 32
    assert scan_row_id("scan:other") != a


def test_scan_row_rejects_unusable_payloads():
    assert scan_row("pt-2", dict(PAYLOAD, address="")) is None
    assert scan_row("pt-3", dict(PAYLOAD, risk_score=None)) is None
    assert scan_row("pt-4", dict(PAYLOAD, risk_score="unknown")) is None


def test_scan_row_defaults_missing_safety_score_to_zero():
    payload = dict(PAYLOAD)
    payload.pop("safety_score")
    row = scan_row("pt-5", payload)
    assert row is not None and row["safety_score"] == 0.0


def test_scan_row_coerces_scalar_risk_flags_to_list():
    row = scan_row("pt-6", dict(PAYLOAD, risk_flags="SENTINEL_LP_LOCK"))
    assert row is not None and row["risk_flags"] == ["SENTINEL_LP_LOCK"]


def test_ensure_table_creates_provenance_columns_and_never_drops():
    client = FakeClient()
    assert ensure_table(client) is not None
    ddl = " ".join(client.commands)
    assert "CREATE TABLE IF NOT EXISTS" in ddl
    for col in ("provenance", "licence", "qdrant_point_id", "risk_flags", "scanned_at"):
        assert col in ddl
    assert "ORDER BY (address, chain_id, doc_id)" in ddl, "must stay address-indexed"
    assert "ReplacingMergeTree" in ddl, "re-run must collapse duplicates"
    assert not any("DROP" in c.upper() for c in client.commands)


def test_insert_rows_sends_positional_rows_in_column_order():
    """Regression: clickhouse_connect raises KeyError on dict rows."""
    client = FakeClient()
    row = scan_row("pt-8", PAYLOAD)
    assert row is not None
    assert insert_rows(client, "wallet_memory", [row]) == 1
    table, rows = client.inserts[0]
    assert table == "wallet_memory.rag_scan_labels"
    assert rows[0] == [row[c] for c in SCAN_COLUMNS]


def test_insert_rows_empty_batch_is_a_noop():
    client = FakeClient()
    assert insert_rows(client, "wallet_memory", []) == 0
    assert client.inserts == []


def test_resolve_address_returns_newest_record():
    got = resolve_address(PAYLOAD["address"], client=FakeClient([ROW]))
    assert isinstance(got, AddressRisk)
    assert (got.risk_score, got.risk_level, got.risk_flags) == (65.0, "high", ["FLAG"])
    assert got.provenance == "qdrant:scan_intel"


def test_resolve_address_uses_indexed_lookup_not_vector_search():
    client = FakeClient([ROW])
    resolve_address(PAYLOAD["address"], client=client)
    sql = client.queries[0][0]
    assert "WHERE address =" in sql
    assert "ORDER BY scanned_at DESC" in sql, "newest scan wins for a re-scanned address"
    assert "similarity" not in sql.lower() and "vector" not in sql.lower()


def test_resolve_address_scopes_by_chain_when_given():
    client = FakeClient([ROW])
    resolve_address(PAYLOAD["address"], chain="base", client=client)
    sql, params = client.queries[0]
    assert "chain_id =" in sql and params["chain"] == "base"


def test_resolve_address_unknown_and_blank_inputs_hit_nothing():
    assert resolve_address("0xdeadbeef", client=FakeClient([])) is None
    client = FakeClient()
    assert resolve_address("   ", client=client) is None
    assert client.queries == [], "blank input must not hit the database"


def test_resolve_address_degrades_to_none_when_query_fails():
    class Boom(FakeClient):
        def query(self, sql: str, parameters: Any = None, **kw: Any) -> FakeResult:
            raise RuntimeError("clickhouse down")

    assert resolve_address("0xabc", client=Boom()) is None


def test_identifiers_outside_allowlist_are_refused():
    """ClickHouse cannot bind identifiers, so the allowlist IS the boundary."""
    assert qualified(db="wallet_memory; DROP TABLE x") is None
    assert qualified(db="wallet_memory", table="wallet_labels") is None
    assert label_counts(db="wallet_memory; DROP TABLE x") == {}


def test_address_risk_serialises_for_agent_tool_output():
    rec = AddressRisk(
        address="a",
        chain_id="base",
        doc_id="d",
        risk_score=9.0,
        risk_level="low",
        safety_score=91.0,
    )
    out = rec.to_dict()
    assert out["risk_flags"] == []
    assert out["risk_level"] == "low"
    assert out["found"] is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
