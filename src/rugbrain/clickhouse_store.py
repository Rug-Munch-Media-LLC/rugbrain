"""ClickHouse connection + schema for the address-label store.

Follows the existing fleet pattern (alerts/clickhouse.py, fear_index/storage.py):
env-driven config, lazily imported clickhouse_connect, same port fallback. No
second client, no second config path. The resolver lives in
``rugbrain.clickhouse_labels``; this module is only the plumbing and the DDL.

SAFETY: the ``rmi`` user can CREATE TABLE in ``wallet_memory`` but NOT CREATE
DATABASE and NOT DROP anything. Nothing here drops.
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any

log = logging.getLogger("rugbrain.clickhouse_store")

# Fleet defaults, mirroring alerts/config.py verbatim.
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_INTERFACE = os.getenv("CLICKHOUSE_INTERFACE", "http")
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "rmi")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "rmi")
# wallet_memory is the only database the rmi user may CREATE TABLE in.
CLICKHOUSE_FALLBACK_DATABASE = os.getenv("CLICKHOUSE_FALLBACK_DATABASE", "wallet_memory")

SCAN_TABLE = os.getenv("CLICKHOUSE_SCAN_LABELS_TABLE", "rag_scan_labels")
PROVENANCE = "qdrant:scan_intel"
LICENCE = "internal-derived; repo LICENSE (MIT) + upstream provider terms"

SCAN_COLUMNS = (
    "row_id",
    "address",
    "chain_id",
    "doc_id",
    "kind",
    "symbol",
    "name",
    "risk_score",
    "safety_score",
    "risk_level",
    "risk_flags",
    "scanned_at",
    "source",
    "provenance",
    "licence",
    "qdrant_point_id",
)

SCAN_DDL = """
CREATE TABLE IF NOT EXISTS {db}.{table} (
    row_id String,
    address String,
    chain_id LowCardinality(String),
    doc_id String,
    kind LowCardinality(String),
    symbol String DEFAULT '',
    name String DEFAULT '',
    risk_score Float32,
    safety_score Float32,
    risk_level LowCardinality(String),
    risk_flags Array(String),
    scanned_at DateTime64(3, 'UTC'),
    source LowCardinality(String),
    provenance LowCardinality(String),
    licence LowCardinality(String),
    qdrant_point_id String
) ENGINE = ReplacingMergeTree(scanned_at)
ORDER BY (address, chain_id, doc_id)
"""

# ClickHouse cannot bind identifiers as query parameters, so db/table names are
# interpolated into SQL. They are module constants, never request input; this
# allowlist makes that fail closed rather than open.
ALLOWED_DATABASES = frozenset({"rmi", "wallet_memory"})
ALLOWED_TABLES = frozenset({"rag_scan_labels"})


def settings() -> dict[str, str]:
    """Settings read at CALL time so a late-loaded .env.rag still wins over the
    import-time constants, without introducing a second config path."""
    return {
        "host": os.getenv("CLICKHOUSE_HOST", CLICKHOUSE_HOST),
        "password": os.getenv("CLICKHOUSE_PASSWORD", CLICKHOUSE_PASSWORD),
        "user": os.getenv("CLICKHOUSE_USER", CLICKHOUSE_USER),
        "database": os.getenv("CLICKHOUSE_DATABASE", CLICKHOUSE_DATABASE),
        "fallback": os.getenv("CLICKHOUSE_FALLBACK_DATABASE", CLICKHOUSE_FALLBACK_DATABASE),
    }


def qualified(db: str | None = None, table: str = SCAN_TABLE) -> str | None:
    """Return a validated ``db.table`` identifier, or None if off-allowlist."""
    resolved = db or settings()["fallback"]
    if resolved not in ALLOWED_DATABASES or table not in ALLOWED_TABLES:
        log.warning("identifier_refused_db=%s_table=%s", resolved, table)
        return None
    return f"{resolved}.{table}"


def load_env_rag(path: str = ".env.rag") -> None:
    """Fill CLICKHOUSE_* from a dotenv file when the environment lacks them."""
    if os.getenv("CLICKHOUSE_PASSWORD"):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("CLICKHOUSE_") and "=" in line:
                    key, _, val = line.strip().partition("=")
                    os.environ.setdefault(key, val)
    except OSError:
        pass


def get_client(database: str | None = None) -> Any:
    """Return a client or None. Same lazy-import + port-fallback shape as
    alerts.ClickHouseAlertLogger. Never raises: callers degrade gracefully."""
    try:
        ch = importlib.import_module("clickhouse_connect")
    except ImportError:
        log.warning("clickhouse_labels_no_driver: install clickhouse-connect")
        return None
    cfg = settings()
    attempts = [CLICKHOUSE_PORT] if CLICKHOUSE_PORT == 8123 else [CLICKHOUSE_PORT, 8123]
    last: Exception | None = None
    for port in attempts:
        try:
            return ch.get_client(
                host=cfg["host"],
                port=port,
                interface=CLICKHOUSE_INTERFACE,
                username=cfg["user"],
                password=cfg["password"],
                database=database or cfg["database"],
            )
        except Exception as exc:  # try next port on failure
            last = exc
    log.warning("clickhouse_labels_unavailable: %s", last)
    return None


def ensure_table(client: Any, table: str = SCAN_TABLE) -> str | None:
    """Create the table if absent; return the database used, or None. Tries the
    configured db then the fleet-writable fallback. Never drops anything."""
    cfg = settings()
    for db in dict.fromkeys([cfg["database"], cfg["fallback"]]):
        if db not in ALLOWED_DATABASES or table not in ALLOWED_TABLES:
            continue
        try:
            client.command(SCAN_DDL.format(db=db, table=table))
            # minmax index for risk_score filters; ADD INDEX supplies the keyword.
            client.command(
                f"ALTER TABLE {db}.{table} ADD INDEX IF NOT EXISTS"
                " idx_risk risk_score TYPE minmax GRANULARITY 4"
            )
            log.info("scan_labels_table_ready db=%s table=%s", db, table)
            return db
        except Exception as exc:
            log.warning("scan_labels_ddl_failed db=%s: %s", db, exc)
    return None
