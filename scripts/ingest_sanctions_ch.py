#!/usr/bin/env python3
"""Extract OFAC SDN digital-currency addresses into wallet_memory.sanctions_crypto.

Source : https://www.treasury.gov/ofac/downloads/sdn.csv (legacy CSV; the /api/download
         endpoint times out). US Government work -> oss_ok.
Parser : marker grammar "Digital Currency Address - <TICKER> <ADDRESS>" in the Remarks
         field (see rugbrain.sanctions_addresses), NOT a blind base58 regex.
Target : wallet_memory.sanctions_crypto (address, address_lower, chain, uid, name,
         program, sdn_type, country, source)

Usage: python3 scripts/ingest_sanctions_ch.py [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.clickhouse_store import get_client, load_env_rag
from rugbrain.dataset_licence import record_licence
from rugbrain.sanctions_addresses import extract_digital_currency

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest_sanctions_ch")

SOURCE = Path(os.getenv("OFAC_SDN_CSV", "/srv/data/raw/ofac/sdn.csv"))
COLUMNS = ["address", "address_lower", "chain", "uid", "name", "program", "sdn_type",
           "country", "source"]
ENT_NUM, NAME, SDN_TYPE, PROGRAM, REMARKS = 0, 1, 2, 3, 11


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    rows: list[list[str]] = []
    entities = 0
    with SOURCE.open(newline="", encoding="latin-1") as handle:
        for record in csv.reader(handle):
            if len(record) < 12:
                continue
            found = extract_digital_currency(record[REMARKS])
            if not found:
                continue
            entities += 1
            for item in found:
                rows.append([
                    item["address"], item["address_lower"], item["chain"],
                    record[ENT_NUM].strip(), record[NAME].replace("-0-", "").strip(),
                    record[PROGRAM].replace("-0-", "").strip(),
                    record[SDN_TYPE].replace("-0-", "").strip(), "", "ofac_sdn",
                ])
    chains: dict[str, int] = {}
    for row in rows:
        chains[row[2]] = chains.get(row[2], 0) + 1
    logger.info("entities=%d addresses=%d chains=%s", entities, len(rows), chains)
    if args.dry_run:
        return 0
    load_env_rag()
    client = get_client(database="wallet_memory")
    client.insert("wallet_memory.sanctions_crypto", rows, column_names=COLUMNS)
    record_licence(
        client,
        dataset="ofac_sdn",
        table="sanctions_crypto",
        licence="US Government work (public domain)",
        status="oss_ok",
        redistribution="allowed",
        source_url="https://www.treasury.gov/ofac/downloads/sdn.csv",
        rows=len(rows),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
