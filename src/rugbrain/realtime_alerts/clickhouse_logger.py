"""Best-effort ClickHouse ``critical_alerts`` ledger."""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any

from .config import (
    CLICKHOUSE_DATABASE,
    CLICKHOUSE_FALLBACK_DATABASE,
    CLICKHOUSE_HOST,
    CLICKHOUSE_INTERFACE,
    CLICKHOUSE_PASSWORD,
    CLICKHOUSE_PORT,
    CLICKHOUSE_USER,
    CRITICAL_ALERTS_TABLE,
)
from .models import CriticalAlert

log = logging.getLogger("rugbrain.realtime_alerts")


class CriticalAlertLogger:
    """Best-effort ClickHouse logger for ``critical_alerts``.

    Mirrors ``rugbrain.alerts.ClickHouseAlertLogger``: lazy import, tries
    the configured DB then the fleet-writable fallback, degrades to a
    warning log when ClickHouse is unreachable.
    """

    DDL = """
    CREATE TABLE IF NOT EXISTS {db}.{table} (
        alert_id String,
        title String,
        severity String,
        chain String,
        source String,
        amount_usd Float64,
        description String,
        tx_hash String,
        addresses String,
        url String,
        detected_at DateTime('UTC'),
        raw String
    ) ENGINE = ReplacingMergeTree()
    ORDER BY (alert_id)
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._db: str | None = None

    def _get(self) -> Any | None:
        if self._client is not None:
            return self._client
        try:
            ch = importlib.import_module("clickhouse_connect")
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
                            client.command(self.DDL.format(db=db, table=CRITICAL_ALERTS_TABLE))
                            self._db = db
                            break
                        except Exception as e:  # try next DB on failure
                            last_err = e
                            client = None
                    if self._db is not None:
                        break
                except Exception as e:  # try next port on failure
                    last_err = e
                    client = None
            if self._db is None:
                raise RuntimeError(f"clickhouse connect failed: {last_err}")
            self._client = client
            log.info(
                "critical_alerts_ready table=%s db=%s",
                CRITICAL_ALERTS_TABLE,
                self._db,
            )
        except Exception as e:
            log.warning("critical_alerts_unavailable: %s", e)
            self._client = False
        return self._client if self._client is not False else None

    def log(self, alert: CriticalAlert) -> bool:
        client = self._get()
        if client is None or self._db is None:
            return False
        try:
            client.insert(
                f"{self._db}.{CRITICAL_ALERTS_TABLE}",
                [
                    [
                        alert.id,
                        alert.title[:500],
                        alert.severity,
                        alert.chain,
                        alert.source,
                        float(alert.amount_usd or 0.0),
                        alert.description[:2000],
                        alert.tx_hash,
                        json.dumps(alert.addresses)[:2000],
                        alert.url[:2000],
                        alert.detected_at,
                        json.dumps(alert.raw, default=str)[:10000],
                    ]
                ],
                column_names=[
                    "alert_id",
                    "title",
                    "severity",
                    "chain",
                    "source",
                    "amount_usd",
                    "description",
                    "tx_hash",
                    "addresses",
                    "url",
                    "detected_at",
                    "raw",
                ],
            )
            return True
        except Exception as e:
            log.warning("critical_alerts_insert_failed: %s", e)
            return False


_critical_logger = CriticalAlertLogger()
