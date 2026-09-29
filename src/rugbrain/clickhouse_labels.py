"""Address-keyed label lookup backed by ClickHouse.

``scan_intel`` holds ~52k points whose payloads are pure structured fields
(address, chain, doc_id, kind, symbol, name, risk_score, safety_score,
risk_level, risk_flags, scanned_at) and ZERO words of prose. They are looked up
by address and filtered by risk_score, never semantically searched, so a vector
index is the wrong home at any dimension. This module maps those payloads to
rows and exposes the resolver the RAG never had: call it with an address, get
risk, flags, labels and provenance back.

Connection and DDL live in ``rugbrain.clickhouse_store`` (the fleet's existing
pattern). Nothing here writes to Qdrant, and nothing anywhere is dropped.

SCOPE LIMIT: cross-address entity resolution is NOT solved here. ``entity_labels``
(204k rows) is not bridged in -- that is separate, larger work.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from .clickhouse_store import (
    LICENCE,
    PROVENANCE,
    SCAN_COLUMNS,
    SCAN_TABLE,
    get_client,
    qualified,
    settings,
)

log = logging.getLogger("rugbrain.clickhouse_labels")

_RESOLVE_COLS = (
    "address, chain_id, doc_id, risk_score, risk_level, safety_score,"
    " risk_flags, symbol, name, toString(scanned_at), source, provenance"
)


@dataclass
class AddressRisk:
    """One address' resolved risk record. Cheap to build, cheap to serialise."""

    address: str
    chain_id: str
    doc_id: str
    risk_score: float
    risk_level: str
    safety_score: float
    risk_flags: list[str] = field(default_factory=list)
    symbol: str = ""
    name: str = ""
    scanned_at: str = ""
    source: str = ""
    provenance: str = ""
    found: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def scan_row_id(doc_id: str) -> str:
    """Deterministic key. doc_id is unique per point, so a re-export upserts the
    same rows instead of duplicating them."""
    return hashlib.sha256(doc_id.encode("utf-8")).hexdigest()[:32]


def scan_row(point_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Qdrant payload to an export row, or None if unusable. Pure: no
    network, no clock. No address or no numeric risk_score -> rejected, never
    silently exported as a zero."""
    address = str(payload.get("address") or "").strip()
    try:
        risk = float(payload.get("risk_score"))
    except (TypeError, ValueError):
        return None
    if not address:
        return None
    try:
        safety = float(payload.get("safety_score"))
    except (TypeError, ValueError):
        safety = 0.0
    flags = payload.get("risk_flags") or []
    if not isinstance(flags, list):
        flags = [str(flags)]
    doc_id = str(payload.get("doc_id") or "")
    return {
        "row_id": scan_row_id(doc_id),
        "address": address,
        "chain_id": str(payload.get("chain") or ""),
        "doc_id": doc_id,
        "kind": str(payload.get("kind") or "scan_result"),
        "symbol": str(payload.get("symbol") or ""),
        "name": str(payload.get("name") or ""),
        "risk_score": risk,
        "safety_score": safety,
        "risk_level": str(payload.get("risk_level") or ""),
        "risk_flags": [str(f) for f in flags],
        "scanned_at": str(payload.get("scanned_at") or "1970-01-01T00:00:00+00:00"),
        "source": "rugbrain_scanner",
        "provenance": PROVENANCE,
        "licence": LICENCE,
        "qdrant_point_id": str(point_id),
    }


def insert_rows(
    client: Any, db: str, rows: Iterable[dict[str, Any]], table: str = SCAN_TABLE
) -> int:
    """Insert rows; return count accepted. clickhouse_connect wants POSITIONAL
    row lists, not dicts -- dicts raise KeyError in the block-size sampler."""
    batch = list(rows)
    if not batch:
        return 0
    cols = list(SCAN_COLUMNS)
    client.insert(f"{db}.{table}", [[r[c] for c in cols] for r in batch], column_names=cols)
    return len(batch)


def resolve_address(
    address: str, chain: str | None = None, client: Any | None = None
) -> AddressRisk | None:
    """Resolve one address to its newest risk record. Indexed lookup, not ANN:
    ORDER BY (address, chain_id, doc_id) makes this a primary-key prefix scan, so
    cost is independent of the 40M-row sibling label tables.

    None when the address is unknown OR ClickHouse is unreachable -- callers must
    treat "no label" and "no database" alike: both mean "assert no risk level".
    """
    addr = (address or "").strip()
    if not addr:
        return None
    table = qualified()
    if table is None:
        return None
    cli = get_client(settings()["fallback"]) if client is None else client
    if cli is None:
        return None
    where = "address = %(addr)s"
    params: dict[str, Any] = {"addr": addr}
    if chain:
        where += " AND chain_id = %(chain)s"
        params["chain"] = chain
    sql = f"SELECT {_RESOLVE_COLS} FROM {table} FINAL WHERE {where} ORDER BY scanned_at DESC, doc_id DESC LIMIT 1"  # noqa: S608
    try:
        rows = cli.query(sql, parameters=params).result_rows
    except Exception as exc:
        log.warning(
            "resolve_failed addr_hash=%s: %s", hashlib.sha256(addr.encode()).hexdigest()[:12], exc
        )
        return None
    if not rows:
        return None
    r = rows[0]
    return AddressRisk(
        address=r[0],
        chain_id=r[1],
        doc_id=r[2],
        risk_score=float(r[3]),
        risk_level=str(r[4]),
        safety_score=float(r[5]),
        risk_flags=list(r[6] or []),
        symbol=str(r[7]),
        name=str(r[8]),
        scanned_at=str(r[9]),
        source=str(r[10]),
        provenance=str(r[11]),
    )


def label_counts(db: str | None = None, table: str = SCAN_TABLE) -> dict[str, int]:
    """Row count + distinct addresses for verification. Empty dict if absent or
    if db/table fall outside the allowlist."""
    resolved = qualified(db, table)
    if resolved is None:
        return {}
    try:
        cli = get_client(resolved.split(".")[0])
        if cli is None:
            return {}
        total = cli.query(f"SELECT count() FROM {resolved}").result_rows[0][0]  # noqa: S608
        addrs = cli.query(f"SELECT uniqExact(address) FROM {resolved}").result_rows[0][0]  # noqa: S608
        return {"rows": int(total), "distinct_addresses": int(addrs)}
    except Exception as exc:
        log.warning("label_counts_failed: %s", exc)
        return {}
