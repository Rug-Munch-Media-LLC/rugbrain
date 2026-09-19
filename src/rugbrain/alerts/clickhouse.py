"""Best-effort ClickHouse logger for the historical alert ledger."""

from __future__ import annotations

import importlib
import json
import logging
from typing import Any

from . import config
from .models import SecurityAlert

log = logging.getLogger("rugbrain.alerts")


class ClickHouseAlertLogger:
    """Best-effort ClickHouse logger for the historical alert ledger.

    Lazily imports ``clickhouse_connect`` so the module works without the
    package installed; every method degrades to a warning log on failure.
    """

    DDL = """
    CREATE TABLE IF NOT EXISTS {db}.{table} (
        alert_id String,
        source String,
        title String,
        protocol String,
        severity String,
        attack_vector String,
        chain String,
        amount_lost_usd Float64,
        description String,
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
            attempts = [config.CLICKHOUSE_PORT]
            if config.CLICKHOUSE_PORT != 8123:
                attempts.append(8123)  # fleet convention: HTTP interface on 8123
            client: Any = None
            last_err: Exception | None = None
            for port in attempts:
                try:
                    client = ch.get_client(
                        host=config.CLICKHOUSE_HOST,
                        port=port,
                        interface=config.CLICKHOUSE_INTERFACE,
                        username=config.CLICKHOUSE_USER,
                        password=config.CLICKHOUSE_PASSWORD,
                        database=config.CLICKHOUSE_DATABASE,
                    )
                    # Try the configured DB first, then the fleet-writable
                    # fallback DB (rmi user lacks DDL on the primary).
                    for db in (config.CLICKHOUSE_DATABASE, config.CLICKHOUSE_FALLBACK_DATABASE):
                        try:
                            client.command(self.DDL.format(db=db, table=config.CLICKHOUSE_TABLE))
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
                "clickhouse_alerts_ready table=%s db=%s port=%d",
                config.CLICKHOUSE_TABLE,
                self._db,
                port,
            )
        except Exception as e:
            log.warning("clickhouse_alerts_unavailable: %s", e)
            self._client = False
        return self._client if self._client is not False else None

    def log(self, alert: SecurityAlert) -> bool:
        client = self._get()
        if client is None or self._db is None:
            return False
        try:
            client.insert(
                f"{self._db}.{config.CLICKHOUSE_TABLE}",
                [
                    [
                        alert.id,
                        alert.source,
                        alert.title,
                        alert.protocol,
                        alert.severity,
                        alert.attack_vector,
                        alert.chain,
                        float(alert.amount_lost_usd or 0.0),
                        alert.description,
                        alert.url,
                        alert.detected_at,
                        json.dumps(alert.raw, default=str)[:10000],
                    ]
                ],
                column_names=[
                    "alert_id",
                    "source",
                    "title",
                    "protocol",
                    "severity",
                    "attack_vector",
                    "chain",
                    "amount_lost_usd",
                    "description",
                    "url",
                    "detected_at",
                    "raw",
                ],
            )
            return True
        except Exception as e:
            log.warning("clickhouse_alerts_insert_failed: %s", e)
            return False
