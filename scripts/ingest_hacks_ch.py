#!/usr/bin/env python3
"""Load the DefiLlama hacks list into wallet_memory.hacks.

Source : https://api.llama.fi/hacks (1,314 records at 2026-10-23)
Licence: none published -> internal_only. Attribute + link, never redistribute.
Target : wallet_memory.hacks (hack_date, name, classification, technique, amount,
         chain[], bridge_hack, target_type, returned_funds, source, defillama_id)

Usage: python3 scripts/ingest_hacks_ch.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.clickhouse_store import get_client, load_env_rag
from rugbrain.dataset_licence import record_licence

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest_hacks_ch")

SOURCE = Path(os.getenv("DEFILLAMA_HACKS", "/srv/data/raw/defillama/hacks.json"))
COLUMNS = [
    "hack_date", "name", "classification", "technique", "amount", "chain", "bridge_hack",
    "target_type", "returned_funds", "source", "defillama_id", "language",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    records = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = [
        [
            datetime.fromtimestamp(record["date"], UTC).date(),
            record.get("name") or "",
            (record.get("classification") or "").strip(),
            (record.get("technique") or "").strip(),
            record.get("amount"),
            record.get("chain") or [],
            int(bool(record.get("bridgeHack"))),
            (record.get("targetType") or "").strip(),
            record.get("returnedFunds"),
            (record.get("source") or "").strip(),
            str(record.get("defillamaId") or ""),
            record.get("language"),
        ]
        for record in records
    ]
    dates = [row[0] for row in rows]
    logger.info("parsed rows=%d span=%s..%s", len(rows), min(dates), max(dates))
    if args.dry_run:
        return 0
    load_env_rag()
    client = get_client(database="wallet_memory")
    client.insert("wallet_memory.hacks", rows, column_names=COLUMNS)
    record_licence(
        client,
        dataset="defillama_hacks",
        table="hacks",
        licence="none published",
        status="internal_only",
        redistribution="prohibited - attribute + link only",
        source_url="https://api.llama.fi/hacks",
        rows=len(rows),
        period_start=min(dates),
        period_end=max(dates),
    )
    logger.info("inserted rows=%d", len(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
