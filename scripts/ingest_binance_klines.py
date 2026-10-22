#!/usr/bin/env python3
"""Load Binance public spot klines into wallet_memory.binance_klines.

Source : https://data.binance.vision/data/spot/monthly/klines/<SYMBOL>/1m/<SYMBOL>-1m-<YYYY-MM>.zip
Licence: no data licence published -> internal_only. Do not redistribute.
File   : headerless CSV, 12 columns, timestamps in MICROSECONDS (2025+ files).
Target : wallet_memory.binance_klines

One month of one symbol per call. A 90-day window = 3 monthly calls (or 90 daily
files) per symbol, same command, --month repeated:
    for m in 2026-07 2026-08 2026-09; do
      python3 scripts/ingest_binance_klines.py --symbol SOLUSDT --month $m
    done

Usage: python3 scripts/ingest_binance_klines.py --symbol BTCUSDT --month 2026-09
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.clickhouse_store import load_env_rag, settings
from rugbrain.dataset_licence import record_licence

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest_binance_klines")

RAW = Path(os.getenv("BINANCE_RAW", "/srv/data/raw/binance"))
CONTAINER = os.getenv("CLICKHOUSE_CONTAINER", "rmi-clickhouse")
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
COLUMNS = ["symbol", "open_time", "open", "high", "low", "close", "volume", "close_time",
           "quote_volume", "trades", "taker_base", "taker_quote", "ignore_flag"]


def _stamp(micros: str) -> str:
    return datetime.fromtimestamp(int(micros) / 1_000_000, UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    args = parser.parse_args()
    name = f"{args.symbol}-1m-{args.month}"
    archive = RAW / f"{name}.zip"
    if not archive.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        url = f"{BASE_URL}/{args.symbol}/1m/{name}.zip"
        logger.info("downloading %s", url)
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["/usr/bin/curl", "-sSL", "-m", "300", "-o", str(archive), url], check=True
        )

    load_env_rag()
    cfg = settings()
    os.environ["CLICKHOUSE_PASSWORD"] = cfg["password"] or ""
    columns = ", ".join(f"`{c}`" for c in COLUMNS)
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        ["/usr/bin/docker", "exec", "-i", "-e", "CLICKHOUSE_PASSWORD", CONTAINER, "clickhouse-client",
         "--user", cfg["user"], "-q",
         f"INSERT INTO wallet_memory.binance_klines ({columns}) FORMAT CSV"],
        stdin=subprocess.PIPE, text=True, env=os.environ.copy(),
    )
    rows = 0
    with zipfile.ZipFile(archive) as bundle, bundle.open(bundle.namelist()[0]) as handle:
        for line in io.TextIOWrapper(handle, encoding="utf-8"):
            fields = line.strip().split(",")
            if len(fields) != 12 or not fields[0].isdigit():
                continue  # skips a leading header row when Binance ships one
            out = [
                args.symbol, _stamp(fields[0]), fields[1], fields[2], fields[3], fields[4],
                fields[5], _stamp(fields[6]), fields[7], fields[8], fields[9], fields[10],
                fields[11],
            ]
            proc.stdin.write(",".join(out) + "\n")
            rows += 1
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("clickhouse-client failed")
    logger.info("symbol=%s month=%s rows=%d", args.symbol, args.month, rows)
    from rugbrain.clickhouse_store import get_client

    client = get_client(database="wallet_memory")
    record_licence(
        client=client,
        dataset="binance_spot_klines",
        table="binance_klines",
        licence="none published",
        status="internal_only",
        redistribution="prohibited - do not redistribute",
        source_url=f"{BASE_URL}/{args.symbol}/1m",
        rows=rows,
        period_start=datetime.strptime(args.month + "-01", "%Y-%m-%d").date(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
