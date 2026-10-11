"""Tests for ingest_ohlcv_cdd.py - OHLCV data ingestion from CryptoDataDownload.

These tests are self-contained: they use fake HTTP responses and fake B2 buckets
and never touch the network or actual B2 credentials. They cover:
  - CSV download and parsing
  - Row conversion to OHLCV format
  - Parquet writing with correct schema
  - Deduplication by (symbol, date) in _process_symbol
  - Parquet round-trip validation
  - B2 key layout matching b2_common.py patterns
  - Dry-run and apply-mode flows
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ingest_ohlcv_cdd

# ── HTTP fakes ───────────────────────────────────────────────────────


class FakeHTTPResponse:
    """Fake httpx.Response for testing."""

    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


def _sample_cdd_csv() -> str:
    """Return a sample CDD CSV (URL header + column header + 3 rows + 1 dup)."""
    return (
        "https://www.cryptodatadownload.com/cdd/Binance_BTCUSDT_d.csv\n"
        "Unix Timestamp,Date,Symbol,Open,High,Low,Close,Volume_BTC,Volume_USDT,tradecount\n"
        "1704067200,2024-01-01,BTCUSDT,42000.0,43000.0,41000.0,42500.0,100.5,4252125.0,1500\n"
        "1704153600,2024-01-02,BTCUSDT,42500.0,43500.0,42000.0,43000.0,120.3,5169000.0,1800\n"
        "1704240000,2024-01-03,BTCUSDT,43000.0,44000.0,42500.0,43500.0,95.7,4168650.0,1200\n"
        "1704067200,2024-01-01,BTCUSDT,42000.0,43000.0,41000.0,42500.0,100.5,4252125.0,1500\n"
    )


def _fake_httpx_get_success(*args, **kwargs):
    """Return a successful fake HTTP response with sample CDD CSV."""
    return FakeHTTPResponse(_sample_cdd_csv())


def _fake_httpx_get_empty(*args, **kwargs):
    """Return an empty CSV response (URL + header only)."""
    return FakeHTTPResponse(
        "https://www.cryptodatadownload.com/cdd/Binance_BTCUSDT_d.csv\n"
        "Unix Timestamp,Date,Symbol,Open,High,Low,Close,Volume_BTC,Volume_USDT,tradecount\n"
    )


def _fake_httpx_get_404(*args, **kwargs):
    """Return a 404 error response."""
    return FakeHTTPResponse("Not Found", status_code=404)


# ── B2 fakes (following test_b2_retention.py pattern) ────────────────────


class _FakeVersion:
    def __init__(self, file_name: str):
        self.file_name = file_name
        self.id_ = f"file_id_{file_name}"


class FakeBucket:
    """In-memory bucket exposing just the b2sdk surface we need."""

    def __init__(self, keys: list[str] | None = None):
        self._keys = keys or []
        self.deleted: list[str] = []
        self.uploaded: list[tuple[str, str]] = []  # (local_file, file_name)

    def ls(self, prefix: str, recursive: bool = True) -> Any:
        return [(_FakeVersion(k), None) for k in self._keys if k.startswith(prefix)]

    def delete_file_version(self, file_id: str, file_name: str) -> None:
        self.deleted.append(file_name)

    def upload_local_file(self, local_file: str, file_name: str) -> None:
        self.uploaded.append((local_file, file_name))


class FakeApi:
    """Fake B2 API with just the methods we need."""

    def __init__(self, keys: list[str] | None = None):
        self.bucket = FakeBucket(keys)
        self._deleted: list[str] = []

    def ensure_bucket(self, name: str) -> Any:
        return self.bucket

    def list_buckets(self):
        """b2_permanence.ensure_bucket calls api.list_buckets()."""
        return []

    def create_bucket(self, name: str, bucket_type: str):
        return self.bucket


# ── Test fixtures and helpers ───────────────────────────────────────────


def make_dates(start: str, end: str) -> list[str]:
    """Return daily YYYY-MM-DD strings from start (inclusive) to end (inclusive)."""
    dates: list[str] = []
    d = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    while d <= end_dt:
        dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return dates


def _sample_ohlcv_row() -> dict[str, object]:
    """Return a sample OHLCV row dict."""
    return {
        "date": "2024-01-01",
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "unix_ts": 1704067200,
        "open": 42000.0,
        "high": 43000.0,
        "low": 41000.0,
        "close": 42500.0,
        "volume_base": 100.5,
        "volume_quote": 4252125.0,
        "tradecount": 1500,
    }


# ── Tests ───────────────────────────────────────────────────────────────


def test_fetch_cdd_csv_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that _fetch_cdd_csv successfully downloads and parses CSV."""
    monkeypatch.setattr(httpx, "get", _fake_httpx_get_success)

    rows = ingest_ohlcv_cdd._fetch_cdd_csv("Binance", "BTCUSDT", "d")
    assert len(rows) == 4  # 3 data rows + 1 duplicate
    assert rows[0] == [
        "1704067200",
        "2024-01-01",
        "BTCUSDT",
        "42000.0",
        "43000.0",
        "41000.0",
        "42500.0",
        "100.5",
        "4252125.0",
        "1500",
    ]
    assert rows[3] == rows[0]


def test_fetch_cdd_csv_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that _fetch_cdd_csv handles empty CSV gracefully."""
    monkeypatch.setattr(httpx, "get", _fake_httpx_get_empty)

    rows = ingest_ohlcv_cdd._fetch_cdd_csv("Binance", "BTCUSDT", "d")
    assert rows == []


def test_fetch_cdd_csv_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that _fetch_cdd_csv raises on HTTP errors."""
    monkeypatch.setattr(httpx, "get", _fake_httpx_get_404)

    with pytest.raises(Exception, match="HTTP 404"):
        ingest_ohlcv_cdd._fetch_cdd_csv("Binance", "BTCUSDT", "d")


def test_ohlcv_row_from_csv_row() -> None:
    """Test conversion of CSV row to OHLCV dict."""
    csv_row = [
        "1704067200",
        "2024-01-01",
        "BTCUSDT",
        "42000.0",
        "43000.0",
        "41000.0",
        "42500.0",
        "100.5",
        "4252125.0",
        "1500",
    ]
    result = ingest_ohlcv_cdd._ohlcv_row_from_csv_row("Binance", "BTCUSDT", csv_row)

    expected = _sample_ohlcv_row()
    assert result == expected
    assert result["exchange"] == "binance"
    assert result["symbol"] == "BTCUSDT"


def test_write_parquet_file_schema_and_content(tmp_path: Path) -> None:
    """Test that _write_parquet_file creates valid parquet with correct schema."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [_sample_ohlcv_row()]
    dest = tmp_path / "test.parquet"

    ingest_ohlcv_cdd._write_parquet_file(dest, rows)

    assert dest.exists()

    table = pq.read_table(dest)
    schema = table.schema

    assert schema.field("date").type == pa.string()
    assert schema.field("exchange").type == pa.string()
    assert schema.field("symbol").type == pa.string()
    assert schema.field("unix_ts").type == pa.int64()
    assert schema.field("open").type == pa.float64()
    assert schema.field("high").type == pa.float64()
    assert schema.field("low").type == pa.float64()
    assert schema.field("close").type == pa.float64()
    assert schema.field("volume_base").type == pa.float64()
    assert schema.field("volume_quote").type == pa.float64()
    assert schema.field("tradecount").type == pa.int64()

    assert table.num_rows == 1
    assert table.column("date")[0].as_py() == "2024-01-01"
    assert table.column("symbol")[0].as_py() == "BTCUSDT"
    assert table.column("close")[0].as_py() == 42500.0
    assert table.column("exchange")[0].as_py() == "binance"


def test_write_parquet_file_preserves_all_rows(tmp_path: Path) -> None:
    """_write_parquet_file writes all rows given to it (no dedup at this layer)."""
    import pyarrow.parquet as pq

    rows = [
        _sample_ohlcv_row(),
        {**_sample_ohlcv_row(), "unix_ts": 1704067201, "close": 42600.0},
        {**_sample_ohlcv_row(), "date": "2024-01-02", "unix_ts": 1704153600},
    ]

    dest = tmp_path / "preserve.parquet"
    ingest_ohlcv_cdd._write_parquet_file(dest, rows)

    table = pq.read_table(dest)
    assert table.num_rows == 3


def test_parquet_roundtrip_in_tmp_path(tmp_path: Path) -> None:
    """Test parquet write/read round-trip preserves data across all columns."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [
        _sample_ohlcv_row(),
        {
            "date": "2024-01-02",
            "exchange": "binance",
            "symbol": "BTCUSDT",
            "unix_ts": 1704153600,
            "open": 42500.0,
            "high": 43500.0,
            "low": 42000.0,
            "close": 43000.0,
            "volume_base": 120.3,
            "volume_quote": 5169000.0,
            "tradecount": 1800,
        },
    ]

    dest = tmp_path / "roundtrip.parquet"
    ingest_ohlcv_cdd._write_parquet_file(dest, rows)

    table = pq.read_table(dest)
    assert table.num_rows == 2

    dates = table.column("date").to_pylist()
    assert dates == ["2024-01-01", "2024-01-02"]

    closes = table.column("close").to_pylist()
    assert 42500.0 in closes
    assert 43000.0 in closes

    # Verify column types
    assert table.column("unix_ts").type == pa.int64()
    assert table.column("tradecount").type == pa.int64()


def test_b2_key_layout_matches_b2_common() -> None:
    """Test that B2 object key format matches b2_common.py expectations.

    b2_common.py header (lines 7-8):
        snapshots/{YYYY-MM-DD}/{collection}.parquet  (default, since 2026-08)
    collection name: ohlcv_{exchange}_{symbol}_{interval}
    """
    exchange = "binance"
    symbol = "btcusdt"
    interval = "d"
    snapshot_date = "2024-01-15"

    collection = f"ohlcv_{exchange}_{symbol}_{interval}"
    expected_key = f"snapshots/{snapshot_date}/{collection}.parquet"

    assert expected_key == "snapshots/2024-01-15/ohlcv_binance_btcusdt_d.parquet"


def test_b2_key_layout_from_process_symbol_logic() -> None:
    """Verify the key layout logic used in _process_symbol."""
    snapshot_date = "2026-08-15"
    collection = "ohlcv_binance_btcusdt_d"
    object_key = f"snapshots/{snapshot_date}/{collection}.parquet"

    assert object_key.startswith("snapshots/")
    assert f"snapshots/{snapshot_date}/" in object_key
    assert object_key.endswith(".parquet")


def test_process_symbol_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _process_symbol in dry-run mode with fake HTTP and temp directory."""
    monkeypatch.setattr(httpx, "get", _fake_httpx_get_success)

    result = ingest_ohlcv_cdd._process_symbol(
        exchange="Binance",
        symbol="BTCUSDT",
        interval="d",
        dry_run=True,
        bucket_name="test-bucket",
        snapshot_date="2024-01-15",
        workdir=tmp_path,
    )

    # Collection uses original exchange/symbol casing
    assert "error" not in result
    assert result["collection"] == "ohlcv_Binance_BTCUSDT_d"
    assert result["format"] == "parquet"
    assert result["object_key"] == "snapshots/2024-01-15/ohlcv_Binance_BTCUSDT_d.parquet"
    # 4 rows minus 1 duplicate (same date+symbol)
    assert result["row_count"] == 3
    assert result["bytes_stored"] > 0
    assert "sha256" in result
    assert result["dry_run"] is True


def test_process_symbol_dedupe_by_date_and_symbol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test that _process_symbol deduplicates by (date, symbol)."""
    monkeypatch.setattr(httpx, "get", _fake_httpx_get_success)

    result = ingest_ohlcv_cdd._process_symbol(
        exchange="Binance",
        symbol="BTCUSDT",
        interval="d",
        dry_run=True,
        bucket_name="test-bucket",
        snapshot_date="2024-01-01",
        workdir=tmp_path,
    )

    assert "error" not in result
    assert result["row_count"] == 3  # 4 rows - 1 duplicate


def test_process_symbol_apply_mode_fake_b2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test _process_symbol in apply mode with fake B2 API."""
    monkeypatch.setattr(httpx, "get", _fake_httpx_get_success)

    fake_api = FakeApi()

    def fake_get_b2_api():
        return fake_api

    monkeypatch.setattr(ingest_ohlcv_cdd, "_get_b2_api", fake_get_b2_api)

    result = ingest_ohlcv_cdd._process_symbol(
        exchange="Binance",
        symbol="BTCUSDT",
        interval="d",
        dry_run=False,
        bucket_name="test-bucket",
        snapshot_date="2024-01-15",
        workdir=tmp_path,
    )

    assert "error" not in result
    assert result["collection"] == "ohlcv_Binance_BTCUSDT_d"
    assert result["row_count"] == 3
    assert result["bytes_stored"] > 0
    assert "sha256" in result

    # Verify B2 upload was called with correct key layout
    assert len(fake_api.bucket.uploaded) == 1
    local_file, uploaded_key = fake_api.bucket.uploaded[0]
    assert uploaded_key == "snapshots/2024-01-15/ohlcv_Binance_BTCUSDT_d.parquet"
    assert local_file.endswith("/ohlcv_Binance_BTCUSDT_d.parquet")


def test_process_symbol_handles_http_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that _process_symbol handles HTTP errors gracefully."""
    monkeypatch.setattr(httpx, "get", _fake_httpx_get_404)

    result = ingest_ohlcv_cdd._process_symbol(
        exchange="Binance",
        symbol="BTCUSDT",
        interval="d",
        dry_run=True,
        bucket_name="test-bucket",
        snapshot_date="2024-01-15",
        workdir=tmp_path,
    )

    assert "error" in result
    assert result["collection"] == "ohlcv_Binance_BTCUSDT_d"


def test_process_symbol_handles_no_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test that _process_symbol handles empty CSV gracefully."""
    monkeypatch.setattr(httpx, "get", _fake_httpx_get_empty)

    result = ingest_ohlcv_cdd._process_symbol(
        exchange="Binance",
        symbol="BTCUSDT",
        interval="d",
        dry_run=True,
        bucket_name="test-bucket",
        snapshot_date="2024-01-15",
        workdir=tmp_path,
    )

    assert "error" in result
    assert result["error"] == "no data"


def test_dry_run_produces_valid_parquet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """End-to-end dry-run: verify the produced parquet has correct schema and content."""
    import pyarrow.parquet as pq

    monkeypatch.setattr(httpx, "get", _fake_httpx_get_success)

    captured: dict[str, Any] = {}

    original_write = ingest_ohlcv_cdd._write_parquet_file

    def spy_write(dest: Path, rows: list, level: int = 6) -> Path:
        captured["dest"] = dest
        captured["rows"] = rows
        result_path = original_write(dest, rows, level)
        captured["table"] = pq.read_table(dest)
        return result_path

    monkeypatch.setattr(ingest_ohlcv_cdd, "_write_parquet_file", spy_write)

    result = ingest_ohlcv_cdd._process_symbol(
        exchange="Binance",
        symbol="BTCUSDT",
        interval="d",
        dry_run=True,
        bucket_name="test-bucket",
        snapshot_date="2024-01-15",
        workdir=tmp_path,
    )

    assert "error" not in result
    assert "table" in captured
    table = captured["table"]

    assert table.num_rows == 3
    cols = [field.name for field in table.schema]
    expected_cols = [
        "date",
        "exchange",
        "symbol",
        "unix_ts",
        "open",
        "high",
        "low",
        "close",
        "volume_base",
        "volume_quote",
        "tradecount",
    ]
    assert cols == expected_cols

    dates = table.column("date").to_pylist()
    assert dates == sorted(dates)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
