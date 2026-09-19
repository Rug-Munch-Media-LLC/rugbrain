"""Best-effort ClickHouse ledger for fear-index snapshots.

Lazily imports ``clickhouse_connect`` so the package works without the
package installed; every method degrades to a warning log on failure
(mirrors ``alerts.ClickHouseAlertLogger``).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from .config import (
    CLICKHOUSE_DATABASE,
    CLICKHOUSE_FALLBACK_DATABASE,
    CLICKHOUSE_HOST,
    CLICKHOUSE_INTERFACE,
    CLICKHOUSE_PASSWORD,
    CLICKHOUSE_PORT,
    CLICKHOUSE_TABLE,
    CLICKHOUSE_USER,
)

log = logging.getLogger("rugbrain.fear_index")


class FearIndexStorage:
    """Best-effort ClickHouse ledger for fear-index snapshots."""

    DDL = """
    CREATE TABLE IF NOT EXISTS {db}.{table} (
        ts DateTime64(3, 'UTC'),
        value Float64,
        classification String,
        fng Nullable(Float64),
        social Nullable(Float64),
        onchain Nullable(Float64),
        news Nullable(Float64),
        components String
    ) ENGINE = ReplacingMergeTree()
    ORDER BY (ts)
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._db: str | None = None

    def _get(self) -> Any | None:
        if self._client is not None:
            return self._client
        try:
            ch = __import__("clickhouse_connect")
            attempts = [CLICKHOUSE_PORT]
            if CLICKHOUSE_PORT != 8123:
                attempts.append(8123)
            client: Any = None
            last_err: Exception | None = None
            for port in attempts:
                try:
                    client = ch.get_client(
                        host=CLICKHOUSE_HOST,
                        port=port,
                        interface=CLICKHOUSE_INTERFACE,
                        username=CLICKHOUSE_USER,
                        password=CLICKHOUSE_PASSWORD,
                        database=CLICKHOUSE_DATABASE,
                    )
                    for db in (CLICKHOUSE_DATABASE, CLICKHOUSE_FALLBACK_DATABASE):
                        try:
                            client.command(self.DDL.format(db=db, table=CLICKHOUSE_TABLE))
                            self._db = db
                            break
                        except Exception as e:
                            last_err = e
                            client = None
                    if self._db is not None:
                        break
                except Exception as e:
                    last_err = e
                    client = None
            if self._db is None:
                raise RuntimeError(f"clickhouse connect failed: {last_err}")
            self._client = client
            log.info("fear_index_clickhouse_ready table=%s db=%s", CLICKHOUSE_TABLE, self._db)
        except Exception as e:
            log.warning("fear_index_clickhouse_unavailable: %s", e)
            self._client = False
        return self._client if self._client is not False else None

    def insert(self, snapshot: dict[str, Any]) -> bool:
        """Persist one snapshot row; False on any failure."""
        client = self._get()
        if client is None or self._db is None:
            return False
        try:
            components = snapshot.get("components") or {}
            client.insert(
                f"{self._db}.{CLICKHOUSE_TABLE}",
                [
                    [
                        datetime.now(UTC),
                        float(snapshot.get("value", 50)),
                        str(snapshot.get("classification", "neutral")),
                        components.get("fng"),
                        components.get("social"),
                        components.get("onchain"),
                        components.get("news"),
                        json.dumps(components, default=str)[:5000],
                    ]
                ],
                column_names=[
                    "ts",
                    "value",
                    "classification",
                    "fng",
                    "social",
                    "onchain",
                    "news",
                    "components",
                ],
            )
            return True
        except Exception as e:
            log.warning("fear_index_clickhouse_insert_failed: %s", e)
            return False

    def query_history(self, days: int) -> list[dict[str, Any]]:
        """Recent (ts, value, classification) rows; [] on failure."""
        client = self._get()
        if client is None or self._db is None:
            return []
        try:
            # Table/db come from trusted env config (never user input); the
            # only dynamic predicate value is bound via parameters.
            rows = client.query(
                f"SELECT ts, value, classification FROM {self._db}.{CLICKHOUSE_TABLE} "  # noqa: S608  # nosec B608
                "WHERE ts >= now() - INTERVAL {days:UInt16} DAY ORDER BY ts ASC",
                parameters={"days": max(1, int(days))},
            )
            result: list[dict[str, Any]] = []
            for row in rows.result_rows:
                ts_value = row[0]
                result.append(
                    {
                        "ts": (
                            ts_value.isoformat()
                            if hasattr(ts_value, "isoformat")
                            else str(ts_value)
                        ),
                        "value": float(row[1]),
                        "classification": str(row[2]),
                    }
                )
            return result
        except Exception as e:
            log.warning("fear_index_clickhouse_query_failed: %s", e)
            return []
