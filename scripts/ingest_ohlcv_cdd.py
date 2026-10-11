#!/usr/bin/env python3
"""Ingest CryptoDataDownload OHLCV market data into B2 parquet snapshots.

Source: https://www.cryptodatadownload.com/cdd/[Exchange]_[Symbol]_[interval].csv
Example: https://www.cryptodatadownload.com/cdd/Binance_BTCUSDT_d.csv
Columns: Unix Timestamp, Date, Symbol, Open, High, Low, Close, Volume_{base},
         Volume_{quote}, tradecount

Output: B2 object key ``snapshots/{YYYY-MM-DD}/{collection}.parquet`` where
        collection is ``ohlcv_{exchange}_{symbol}_{interval}`` (e.g.
        ``ohlcv_binance_btcusdt_d``). Each parquet file contains all rows
        for that symbol/interval, with columns:
          date (string YYYY-MM-DD), exchange (string), symbol (string),
          unix_ts (int), open (float), high (float), low (float), close (float),
          volume_base (float), volume_quote (float), tradecount (int).

Uses the same B2 helpers as ``b2_permanence.py`` (get_b2_api, ensure_bucket,
upload_file) but writes a custom OHLCV parquet schema (not the Qdrant-point
schema in b2_common.parquet_schema()).

Default dry-run; pass --apply to actually upload to B2. Requires B2 credentials
for --apply.

See DATA-STRATEGY.md: numeric/analytics data -> B2 parquet (not Qdrant).
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import b2_common

logger = logging.getLogger("ingest_ohlcv_cdd")

# CryptoDataDownload base URL
CDD_BASE = "https://www.cryptodatadownload.com/cdd"

# Default symbols and parameters
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
DEFAULT_EXCHANGE = "Binance"  # URL is case-sensitive
DEFAULT_INTERVAL = "d"  # daily
ZSTD_LEVEL = 6  # matches b2_permanence.py


def _get_b2_api() -> b2_common.B2Api:
    """Authorize B2 client; raise if credentials missing."""
    key_id = os.environ.get("B2_APPLICATION_KEY_ID")
    key = os.environ.get("B2_APPLICATION_KEY")
    if not key_id or not key:
        raise RuntimeError(
            "B2 credentials missing — set B2_APPLICATION_KEY_ID and "
            "B2_APPLICATION_KEY (systemd: EnvironmentFile=/path/to/b2-permanence.env)"
        )
    api = b2_common.B2Api(b2_common.InMemoryAccountInfo())
    api.authorize_account("production", key_id, key)
    return api


def _ensure_bucket(api: b2_common.B2Api, bucket_name: str) -> b2_common.Bucket:
    """Return bucket, creating if missing (private)."""
    for bucket in api.list_buckets():
        if bucket.name == bucket_name:
            return bucket
    return api.create_bucket(bucket_name, "allPrivate")


def _upload_file(api: b2_common.B2Api, bucket_name: str, local_path: Path, key: str) -> None:
    """Upload local_path to bucket_name at B2 object key."""
    bucket = _ensure_bucket(api, bucket_name)
    bucket.upload_local_file(local_file=str(local_path), file_name=key)


def _ohlcv_parquet_schema() -> Any:
    """PyArrow schema for OHLCV parquet snapshots."""
    import pyarrow as pa

    return pa.schema(
        [
            ("date", pa.string()),  # YYYY-MM-DD from CSV
            ("exchange", pa.string()),  # e.g., binance
            ("symbol", pa.string()),  # e.g., BTCUSDT
            ("unix_ts", pa.int64()),  # Unix timestamp (ms)
            ("open", pa.float64()),
            ("high", pa.float64()),
            ("low", pa.float64()),
            ("close", pa.float64()),
            ("volume_base", pa.float64()),  # Volume in base asset (BTC, ETH)
            ("volume_quote", pa.float64()),  # Volume in quote asset (USDT)
            ("tradecount", pa.int64()),  # Number of trades
        ]
    )


def _ohlcv_row_from_csv_row(exchange: str, symbol: str, row: list[str]) -> dict[str, object]:
    """Convert one CSV row (as list of strings) to an OHLCV dict for pyarrow."""
    # CSV columns: Unix Timestamp, Date, Symbol, Open, High, Low, Close,
    # Volume_{base}, Volume_{quote}, tradecount
    unix_ts = int(row[0])
    date_str = row[1]  # already YYYY-MM-DD
    open_ = float(row[3])
    high = float(row[4])
    low = float(row[5])
    close = float(row[6])
    volume_base = float(row[7])
    volume_quote = float(row[8])
    tradecount = int(row[9])

    return {
        "date": date_str,
        "exchange": exchange.lower(),
        "symbol": symbol.upper(),
        "unix_ts": unix_ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume_base": volume_base,
        "volume_quote": volume_quote,
        "tradecount": tradecount,
    }


def _fetch_cdd_csv(exchange: str, symbol: str, interval: str) -> list[list[str]]:
    """Download CryptoDataDownload CSV and return parsed rows (excluding header).

    URL is case-sensitive: exchange must be the proper-cased name
    (e.g. ``Binance``), not lowercased. The collection metadata uses the
    lowercased form for consistency.
    """
    import httpx

    url = f"{CDD_BASE}/{exchange}_{symbol.upper()}_{interval}.csv"
    logger.info("ohlcv_fetch_start url=%s", url)

    try:
        resp = httpx.get(url, timeout=30.0)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("ohlcv_fetch_failed url=%s error=%s", url, exc)
        raise

    # Parse CSV; first line is URL, second is header, data from line 3.
    lines = resp.text.strip().splitlines()
    if not lines:
        logger.warning("ohlcv_empty_response url=%s", url)
        return []

    # Skip first two lines (URL + header)
    start_idx = 0
    if len(lines) >= 2 and lines[0].startswith("https://"):
        start_idx = 2
    elif lines[0].startswith("Unix Timestamp") or lines[0].startswith("Unix"):
        start_idx = 1

    reader = csv.reader(lines[start_idx:])
    rows = [list(row) for row in reader]

    logger.info("ohlcv_fetch_done url=%s rows=%d", url, len(rows))
    return rows


def _write_parquet_file(dest: Path, rows: list[dict[str, object]], level: int = ZSTD_LEVEL) -> Path:
    """Write OHLCV rows to dest as zstd-compressed parquet."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(rows, schema=_ohlcv_parquet_schema())
    pq.write_table(table, dest, compression="zstd", compression_level=level)
    logger.debug("ohlcv_parquet_written path=%s rows=%d", dest, len(rows))
    return dest


def _process_symbol(
    exchange: str,
    symbol: str,
    interval: str,
    dry_run: bool,
    bucket_name: str,
    snapshot_date: str,
    workdir: Path,
) -> dict[str, Any]:
    """Fetch, validate, and write (or upload) OHLCV data for one symbol."""
    started = datetime.now(UTC)

    # Fetch CSV
    try:
        csv_rows = _fetch_cdd_csv(exchange, symbol, interval)
    except Exception as exc:
        logger.error(
            "ohlcv_symbol_failed exchange=%s symbol=%s interval=%s error=%s",
            exchange,
            symbol,
            interval,
            exc,
        )
        return {
            "collection": f"ohlcv_{exchange}_{symbol}_{interval}",
            "error": str(exc),
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
        }

    if not csv_rows:
        logger.warning(
            "ohlcv_no_data exchange=%s symbol=%s interval=%s", exchange, symbol, interval
        )
        return {
            "collection": f"ohlcv_{exchange}_{symbol}_{interval}",
            "error": "no data",
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
        }

    # Convert to OHLCV rows
    ohlcv_rows = [_ohlcv_row_from_csv_row(exchange, symbol, row) for row in csv_rows]

    # Dedupe by (date, symbol) and sort by date
    seen = set()
    unique_rows = []
    for row in ohlcv_rows:
        key = (row["date"], row["symbol"])
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(row)
    unique_rows.sort(key=lambda r: (r["date"], r["unix_ts"]))

    # Prepare collection name and B2 object key
    collection = f"ohlcv_{exchange}_{symbol}_{interval}"
    object_key = f"snapshots/{snapshot_date}/{collection}.parquet"

    if dry_run:
        # Dry-run: write to temp file and log stats
        temp_path = workdir / f"{collection}.parquet"
        try:
            _write_parquet_file(temp_path, unique_rows)
            size = temp_path.stat().st_size
            logger.info(
                "ohlcv_dry_run collection=%s rows=%d size=%s key=%s",
                collection,
                len(unique_rows),
                _fmt_size(size),
                object_key,
            )
            return {
                "collection": collection,
                "format": "parquet",
                "object_key": object_key,
                "row_count": len(unique_rows),
                "bytes_stored": size,
                "sha256": b2_common.sha256_file(temp_path),
                "started_at": started.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
                "dry_run": True,
            }
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
    else:
        # Apply mode: actually upload to B2
        try:
            api = _get_b2_api()
            _ensure_bucket(api, bucket_name)

            # Write to temp file then upload
            temp_path = workdir / f"{collection}.parquet"
            try:
                _write_parquet_file(temp_path, unique_rows)
                _upload_file(api, bucket_name, temp_path, object_key)
                size = temp_path.stat().st_size
                logger.info(
                    "ohlcv_upload_ok collection=%s rows=%d size=%s key=%s",
                    collection,
                    len(unique_rows),
                    _fmt_size(size),
                    object_key,
                )
                return {
                    "collection": collection,
                    "format": "parquet",
                    "object_key": object_key,
                    "row_count": len(unique_rows),
                    "bytes_stored": size,
                    "sha256": b2_common.sha256_file(temp_path),
                    "started_at": started.isoformat(),
                    "finished_at": datetime.now(UTC).isoformat(),
                }
            finally:
                if temp_path.exists():
                    temp_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.error("ohlcv_upload_failed collection=%s error=%s", collection, exc)
            return {
                "collection": collection,
                "error": str(exc),
                "started_at": started.isoformat(),
                "finished_at": datetime.now(UTC).isoformat(),
            }


def _fmt_size(n: int) -> str:
    """Format bytes into human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n //= 1024
    return f"{n:.1f}TB"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest CryptoDataDownload OHLCV data into B2 parquet snapshots."
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help=f"Symbols to ingest (default: {' '.join(DEFAULT_SYMBOLS)})",
    )
    parser.add_argument(
        "--exchange",
        default=DEFAULT_EXCHANGE,
        help=f"Exchange name (default: {DEFAULT_EXCHANGE})",
    )
    parser.add_argument(
        "--interval",
        default=DEFAULT_INTERVAL,
        help=f"Interval (default: {DEFAULT_INTERVAL})",
    )
    parser.add_argument(
        "--start-date",
        help="Start date (inclusive) YYYY-MM-DD; filters CSV rows",
    )
    parser.add_argument(
        "--end-date",
        help="End date (inclusive) YYYY-MM-DD; filters CSV rows",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Fetch and validate but do not upload (default)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually upload to B2 (requires B2 credentials)",
    )
    parser.add_argument(
        "--b2-bucket",
        default=None,
        help=f"B2 bucket override (default: {b2_common.DEFAULT_BUCKET})",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Load KEY=value env file (e.g. /path/to/b2-permanence.env)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Debug logging",
    )
    args = parser.parse_args()

    # Resolve dry/apply mode
    dry_run = not args.apply
    if args.apply and args.dry_run:
        logger.warning("--apply overrides --dry-run; enabling upload mode")
        dry_run = False

    # Load env file if provided
    if args.env_file:
        b2_common.load_env_file(args.env_file)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logging.getLogger("b2sdk").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Determine snapshot date (today)
    snapshot_date = datetime.now(UTC).strftime("%Y-%m-%d")

    # Resolve bucket
    bucket_name = args.b2_bucket or os.environ.get("B2_BUCKET_NAME", b2_common.DEFAULT_BUCKET)

    # Log startup
    logger.info("=== OHLCV CDD ingest started ===")
    logger.info(
        "exchange=%s symbols=%s interval=%s snapshot_date=%s bucket=%s dry_run=%s",
        args.exchange,
        " ".join(args.symbols),
        args.interval,
        snapshot_date,
        bucket_name,
        dry_run,
    )

    # Process each symbol - use secure temporary directory
    workdir = Path(tempfile.mkdtemp(prefix="ohlcv_ingest_"))

    entries: list[dict[str, Any]] = []
    failures = 0
    for symbol in args.symbols:
        logger.info("[processing] symbol=%s", symbol)
        try:
            result = _process_symbol(
                exchange=args.exchange,
                symbol=symbol,
                interval=args.interval,
                dry_run=dry_run,
                bucket_name=bucket_name,
                snapshot_date=snapshot_date,
                workdir=workdir,
            )
            entries.append(result)
            if "error" in result:
                failures += 1
        except Exception as exc:
            logger.error("symbol_process_failed symbol=%s error=%s", symbol, exc)
            entries.append(
                {
                    "collection": f"ohlcv_{args.exchange}_{symbol}_{args.interval}",
                    "error": str(exc),
                    "started_at": datetime.now(UTC).isoformat(),
                    "finished_at": datetime.now(UTC).isoformat(),
                }
            )
            failures += 1

    # Summary
    successful = [e for e in entries if "error" not in e]
    total_rows = sum(int(e.get("row_count", 0)) for e in successful)
    total_size = sum(int(e.get("bytes_stored", 0)) for e in successful)

    logger.info(
        "=== OHLCV CDD ingest complete: %d ok, %d failed, %d rows, %s total ===",
        len(successful),
        failures,
        total_rows,
        _fmt_size(total_size),
    )

    if failures > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
