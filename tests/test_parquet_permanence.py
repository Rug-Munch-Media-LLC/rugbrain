"""Tests for the parquet + DuckDB RAG permanence pipeline.

Covers:
  - b2_common parquet helpers (row conversion, write/read round-trip)
  - b2_restore parquet/NDJSON decoding back to Qdrant points
  - migrate_to_parquet NDJSON → parquet conversion
  - parquet_analytics read-only SQL guard, JSON-safe rows, DuckDB queries

These tests are self-contained: they write parquet to temp files and
query them with DuckDB locally — no B2, Qdrant, or network required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import b2_common  # noqa: E402
import b2_restore  # noqa: E402
import migrate_to_parquet  # noqa: E402

# Re-exported for use by later modules in this file
__all__ = ["b2_common", "b2_restore", "migrate_to_parquet"]


# ── Helpers ─────────────────────────────────────────────────────────────


class FakePoint:
    """Minimal stand-in for a qdrant scroll Record."""

    def __init__(self, pid: object, vector: object = None, payload: object = None):
        self.id = pid
        self.vector = vector
        self.payload = payload if payload is not None else {}


# ── b2_common: row conversion ──────────────────────────────────────────


def test_point_to_row_flat_vector():
    point = FakePoint(
        pid=123,
        vector=[0.1, 0.2, 0.3],
        payload={"content": "hello", "metadata": {"severity": "high"}, "source": "x"},
    )
    row = b2_common.point_to_row(point)
    assert row["id"] == "123"
    assert row["embedding"] == [0.1, 0.2, 0.3]
    assert row["vectors"] is None
    assert row["content"] == "hello"
    assert json.loads(row["metadata"]) == {"severity": "high"}
    assert json.loads(row["payload"])["source"] == "x"


def test_point_to_row_uuid_and_named_vectors():
    point = FakePoint(
        pid="07ba6f51-a3a9-5387-b6aa-3baa5f267986",
        vector={"dense": [1.0, 2.0], "bm25": {"indices": [0, 2], "values": [0.5, 0.25]}},
        payload={"content": "hybrid"},
    )
    row = b2_common.point_to_row(point)
    assert row["id"] == "07ba6f51-a3a9-5387-b6aa-3baa5f267986"
    assert row["embedding"] == [1.0, 2.0]
    vectors = json.loads(row["vectors"])
    assert vectors["bm25"]["indices"] == [0, 2]


def test_point_to_row_no_vectors():
    point = FakePoint(pid=7, vector=[9.9], payload={"content": "x"})
    row = b2_common.point_to_row(point, with_vectors=False)
    assert row["embedding"] is None
    assert row["vectors"] is None


def test_point_to_row_non_dict_payload():
    point = FakePoint(pid=1, vector=[1.0], payload=None)
    row = b2_common.point_to_row(point)
    assert row["payload"] == "{}"
    assert row["content"] == ""


# ── b2_common: parquet write / DuckDB read round-trip ──────────────────


def test_parquet_write_read_roundtrip(tmp_path):
    rows = [
        {
            "id": "123",
            "content": "first",
            "metadata": json.dumps({"severity": "high"}),
            "payload": json.dumps({"content": "first", "severity": "high"}),
            "embedding": [0.1, 0.2, 0.3],
            "vectors": None,
        },
        {
            "id": "07ba6f51-a3a9-5387-b6aa-3baa5f267986",
            "content": "second",
            "metadata": json.dumps({}),
            "payload": json.dumps({"content": "second"}),
            "embedding": None,
            "vectors": json.dumps({"dense": [1.0], "bm25": {"indices": [0], "values": [1.0]}}),
        },
    ]
    dest = tmp_path / "test.parquet"
    b2_common.write_parquet_file(dest, rows, level=19)

    records = b2_common.read_parquet_records(dest)
    assert len(records) == 2
    assert records[0]["id"] == "123"
    assert list(records[0]["embedding"]) == [0.1, 0.2, 0.3]
    assert records[1]["id"] == "07ba6f51-a3a9-5387-b6aa-3baa5f267986"
    assert records[1]["vectors"] == json.dumps(
        {"dense": [1.0], "bm25": {"indices": [0], "values": [1.0]}}
    )


def test_parquet_stream_roundtrip(tmp_path):
    dest = tmp_path / "stream.parquet"
    batches = [
        [
            {
                "id": str(i),
                "content": f"c{i}",
                "metadata": "{}",
                "payload": "{}",
                "embedding": [float(i)],
                "vectors": None,
            }
            for i in range(0, 50)
        ]
    ]
    b2_common.stream_parquet(dest, batches)
    records = b2_common.read_parquet_records(dest)
    assert len(records) == 50
    assert records[49]["id"] == "49"


# ── b2_restore: record → PointStruct ───────────────────────────────────


def test_record_to_point_parquet_row():
    rec = {
        "id": "07ba6f51-a3a9-5387-b6aa-3baa5f267986",
        "content": "hello",
        "metadata": json.dumps({"severity": "high"}),
        "payload": json.dumps({"content": "hello", "severity": "high"}),
        "embedding": [0.1, 0.2],
        "vectors": None,
    }
    point = b2_restore.record_to_point(rec)
    assert str(point.id) == "07ba6f51-a3a9-5387-b6aa-3baa5f267986"
    assert point.vector == [0.1, 0.2]
    assert point.payload["severity"] == "high"


def test_record_to_point_named_vectors():
    rec = {
        "id": "42",
        "content": "",
        "metadata": "{}",
        "payload": "{}",
        "embedding": [1.0],
        "vectors": json.dumps({"dense": [1.0], "bm25": {"indices": [0], "values": [0.5]}}),
    }
    point = b2_restore.record_to_point(rec)
    assert point.id == 42
    # qdrant_client coerces nested dicts to SparseVector; the named dict is preserved.
    assert point.vector["dense"] == [1.0]
    assert point.vector["bm25"].values == [0.5]


def test_parse_point_id():
    assert b2_restore.parse_point_id(123) == 123
    assert b2_restore.parse_point_id("123") == 123
    assert (
        str(b2_restore.parse_point_id("07ba6f51-a3a9-5387-b6aa-3baa5f267986"))
        == "07ba6f51-a3a9-5387-b6aa-3baa5f267986"
    )
    with pytest.raises(ValueError):
        b2_restore.parse_point_id("not-a-valid-id")


def test_parse_ndjson_legacy(tmp_path):
    path = tmp_path / "points.jsonl"
    path.write_text(
        json.dumps({"id": "123", "vector": [0.5], "payload": {"content": "a"}})
        + "\n"
        + json.dumps(
            {
                "id": "07ba6f51-a3a9-5387-b6aa-3baa5f267986",
                "vector": [0.9],
                "payload": {"content": "b"},
            }
        )
        + "\n"
    )
    points = b2_restore.parse_ndjson(path)
    assert len(points) == 2
    assert points[0].id == 123
    assert points[1].payload["content"] == "b"


def test_restore_prefers_parquet_entry():
    ndjson = {"collection": "x", "format": "ndjson", "object_key": "x.jsonl.zst.enc"}
    parquet = {"collection": "x", "format": "parquet", "object_key": "x.parquet"}
    assert b2_restore._prefer_format(parquet, ndjson)
    assert not b2_restore._prefer_format(ndjson, parquet)
    assert not b2_restore._prefer_format(parquet, parquet)


# ── migrate_to_parquet: NDJSON → parquet ───────────────────────────────


def test_ndjson_records_to_rows(tmp_path):
    src = tmp_path / "legacy.jsonl"
    src.write_text(
        json.dumps(
            {"id": 5, "vector": [1.0, 2.0], "payload": {"content": "x", "metadata": {"a": 1}}}
        )
        + "\n"
    )
    rows = migrate_to_parquet.ndjson_records_to_rows(src)
    assert len(rows) == 1
    assert rows[0]["id"] == "5"
    assert rows[0]["embedding"] == [1.0, 2.0]
    assert json.loads(rows[0]["metadata"]) == {"a": 1}


def test_legacy_object_format():
    assert migrate_to_parquet.legacy_object_format("x.jsonl.zst.enc") == "zstd-encrypted"
    assert migrate_to_parquet.legacy_object_format("x.jsonl.zst") == "zstd"
    assert migrate_to_parquet.legacy_object_format("x.jsonl") == "plain"
    assert migrate_to_parquet.legacy_object_format("x.parquet") is None


def test_collection_from_legacy_key():
    assert (
        migrate_to_parquet.collection_from_legacy_key(
            "snapshots/2026-08-01/threat_intel.jsonl.zst.enc"
        )
        == "threat_intel"
    )
    assert (
        migrate_to_parquet.collection_from_legacy_key("snapshots/2026-08-01/news.jsonl.zst")
        == "news"
    )


# ── parquet_analytics: SQL guard + JSON safety ─────────────────────────


def test_validate_read_only_sql_accepts():
    from rmi_rag.parquet_analytics import validate_read_only_sql

    validate_read_only_sql("SELECT count(*) FROM snapshots")
    validate_read_only_sql("WITH x AS (SELECT * FROM snapshots) SELECT * FROM x LIMIT 5;")
    validate_read_only_sql("DESCRIBE snapshots")
    validate_read_only_sql("SHOW TABLES")


def test_validate_read_only_sql_rejects():
    from rmi_rag.parquet_analytics import validate_read_only_sql

    for bad in (
        "INSERT INTO snapshots VALUES (1)",
        "UPDATE snapshots SET content = 'x'",
        "DELETE FROM snapshots",
        "DROP TABLE snapshots",
        "CREATE TABLE t (a int)",
        "ATTACH 'x.db'",
        "COPY snapshots TO 'out.parquet'",
        "INSTALL httpfs",
        "LOAD httpfs",
        "PRAGMA version",
        "SET memory_limit='1GB'",
        "SELECT * FROM snapshots; SELECT * FROM other",
        "SELECT * FROM read_parquet('https://evil.example/x.parquet')",
        "SELECT * FROM 's3://other-bucket/x.parquet'",
    ):
        with pytest.raises(ValueError, match=r"Only read-only|disallowed|external|Multiple"):
            validate_read_only_sql(bad)


def test_json_safe_row():
    from rmi_rag.parquet_analytics import json_safe_row

    assert json_safe_row((1, "a", 2.5, None, True)) == [1, "a", 2.5, None, True]
    row = json_safe_row((float("nan"), [1.0, float("nan")], {"k": 1}, b"bytes"))
    assert row[0] is None
    assert row[1][1] is None
    assert row[2] == {"k": 1}
    assert row[3] == "b'bytes'"


# ── parquet_analytics: DuckDB query over local parquet ─────────────────


def test_run_snapshot_query_local_glob(tmp_path):
    from rmi_rag.parquet_analytics import run_snapshot_query

    rows = [
        {
            "id": "1",
            "content": "Euler Finance exploited for $200M",
            "metadata": json.dumps({"year": 2024, "amount": 200000000, "severity": "critical"}),
            "payload": json.dumps({"content": "Euler", "year": 2024}),
            "embedding": [0.1, 0.2, 0.3],
            "vectors": None,
        },
        {
            "id": "2",
            "content": "Small phishing campaign",
            "metadata": json.dumps({"year": 2025, "amount": 5000, "severity": "low"}),
            "payload": json.dumps({"content": "phishing", "year": 2025}),
            "embedding": [0.4, 0.5, 0.6],
            "vectors": None,
        },
    ]
    dest = tmp_path / "threat_intel.parquet"
    b2_common.write_parquet_file(dest, rows)

    result = run_snapshot_query(
        "SELECT id, content FROM snapshots "
        "WHERE json_extract_string(metadata, '$.severity') = 'critical'",
        glob_override=str(tmp_path / "*.parquet"),
    )
    assert result["source"] == "b2-direct"
    assert result["row_count"] == 1
    assert result["rows"][0][1] == "Euler Finance exploited for $200M"

    # count over the view
    result2 = run_snapshot_query(
        "SELECT count(*) AS n FROM snapshots", glob_override=str(tmp_path / "*.parquet")
    )
    assert result2["rows"][0][0] == 2

    # truncation
    result3 = run_snapshot_query(
        "SELECT * FROM snapshots", limit=1, glob_override=str(tmp_path / "*.parquet")
    )
    assert result3["row_count"] == 1
    assert result3["truncated"] is True
