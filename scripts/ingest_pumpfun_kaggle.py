#!/usr/bin/env python3
"""Load the Kaggle pump.fun launch + price-trajectory dataset (CC0-1.0) into ClickHouse.

Source : kaggle danmina/solana-pump-fun-launches-and-price-trajectories
         period 2026-09-08 .. 2026-10-22, licence CC0-1.0 (oss_ok)
Files  : launches.csv, observations.csv, ai_verdicts.csv, migrations.csv
Target : wallet_memory.pumpfun_{launches,observations,ai_verdicts,migrations}
Join   : mint (observations additionally on launched_at)

Rows are streamed as CSV into clickhouse-client (inside rmi-clickhouse); empty fields
become \\N -> NULL, so partial rows stay honestly NULL instead of silently 0.

Usage: python3 scripts/ingest_pumpfun_kaggle.py [--files launches,observations] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.clickhouse_store import load_env_rag, settings
from rugbrain.dataset_licence import record_licence

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest_pumpfun_kaggle")

SRC = Path(os.getenv("PUMPFUN_SRC", "/srv/data/raw/pumpfun"))
CONTAINER = os.getenv("CLICKHOUSE_CONTAINER", "rmi-clickhouse")
DATASET = "kaggle_danmina_pumpfun"
SOURCE_URL = "https://www.kaggle.com/datasets/danmina/solana-pump-fun-launches-and-price-trajectories"
TIME_COLS = {"seen_at", "launched_at", "observed_at", "decided_at", "migrated_at"}
FILES = {
    "launches": "launches.csv",
    "observations": "observations.csv",
    "ai_verdicts": "ai_verdicts.csv",
    "migrations": "migrations.csv",
}


def _normalise(row: list[str], header: list[str]) -> list[str]:
    out = []
    for value, column in zip(row, header, strict=False):
        if value == "":
            out.append("\\N")
        elif column in TIME_COLS:
            out.append(datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M:%S.%f"))
        else:
            out.append(value)
    return out


def _load_one(name: str, dry_run: bool) -> tuple[int, date | None, date | None]:
    path = SRC / FILES[name]
    table = f"pumpfun_{name}"
    cfg = settings()
    with path.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    time_index = next((i for i, c in enumerate(header) if c in TIME_COLS), None)
    columns = ", ".join(f"`{c}`" for c in header)
    command = [
        "/usr/bin/docker", "exec", "-i", "-e", "CLICKHOUSE_PASSWORD", CONTAINER,
        "clickhouse-client",
        "--user", cfg["user"],
        "--input_format_null_as_default=1",
        "--input_format_csv_empty_as_default=1",
        "--date_time_input_format=best_effort",
        "-q", f"INSERT INTO wallet_memory.{table} ({columns}) FORMAT CSV",
    ]
    rows = 0
    period_start: date | None = None
    period_end: date | None = None
    proc = None if dry_run else subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        command, stdin=subprocess.PIPE, text=True, env=os.environ.copy()
    )
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)
        for raw in reader:
            clean = _normalise(raw, header)
            if time_index is not None:
                stamp = clean[time_index]
                if stamp != "\\N":
                    day = stamp[:10]
                    period_start = min(period_start or date.fromisoformat(day), date.fromisoformat(day))
                    period_end = max(period_end or date.fromisoformat(day), date.fromisoformat(day))
            if proc is not None:
                csv.writer(proc.stdin, quoting=csv.QUOTE_MINIMAL, lineterminator="\n").writerow(clean)
            rows += 1
    if proc is not None:
        proc.stdin.close()
        if proc.wait() != 0:
            raise SystemExit(f"clickhouse-client failed for {table}")
    logger.info("loaded table=%s rows=%d", table, rows)
    return rows, period_start, period_end


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", default=",".join(FILES), help="comma list of subsets")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    load_env_rag()
    os.environ["CLICKHOUSE_PASSWORD"] = settings()["password"] or ""
    from rugbrain.clickhouse_store import get_client

    client = None if args.dry_run else get_client(database="wallet_memory")
    for name in (n.strip() for n in args.files.split(",") if n.strip()):
        if name not in FILES:
            raise SystemExit(f"unknown subset {name!r}; choices: {', '.join(FILES)}")
        rows, start, end = _load_one(name, args.dry_run)
        if client is not None:
            record_licence(
                client,
                dataset=DATASET,
                table=f"pumpfun_{name}",
                licence="CC0-1.0",
                status="oss_ok",
                redistribution="allowed",
                source_url=SOURCE_URL,
                rows=rows,
                period_start=start,
                period_end=end,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
