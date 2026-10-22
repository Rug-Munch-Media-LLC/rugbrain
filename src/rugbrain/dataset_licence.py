"""Provenance ledger for ingested external datasets (wallet_memory.dataset_licences).

One row per (dataset, table) pair. `licence_status` is the contract: anything that is
not `oss_ok` is `internal_only` and must never be redistributed.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

logger = logging.getLogger("rugbrain.dataset_licence")

TABLE = "wallet_memory.dataset_licences"
COLUMNS = [
    "dataset",
    "table_name",
    "licence",
    "licence_status",
    "redistribution",
    "source_url",
    "rows",
    "period_start",
    "period_end",
    "ingested_at",
]


def record_licence(
    client: Any,
    *,
    dataset: str,
    table: str,
    licence: str,
    status: str,
    redistribution: str,
    source_url: str,
    rows: int,
    period_start: date | None = None,
    period_end: date | None = None,
) -> None:
    """Upsert one provenance row (ReplacingMergeTree on ingested_at)."""
    if status not in {"oss_ok", "internal_only", "blocked"}:
        raise ValueError(f"unknown licence status: {status}")
    client.insert(
        TABLE,
        [
            [
                dataset,
                table,
                licence,
                status,
                redistribution,
                source_url,
                rows,
                period_start,
                period_end,
                datetime.now(UTC),
            ]
        ],
        column_names=COLUMNS,
    )
    logger.info("licence_recorded dataset=%s table=%s status=%s rows=%d", dataset, table, status, rows)
