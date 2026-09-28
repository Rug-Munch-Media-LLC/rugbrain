"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Driver construction and the identity rules the graph is keyed on.

**Why this exists rather than graphiti.** There is no graphiti deployment
in this fleet (verified: zero hits in the live opencode config; the only
matches on the host are stale config backups). What *does* exist is a
Neo4j 5 instance already carrying graphiti's signature schema — `Episodic`
nodes with an `entity_edges` array, `Entity` nodes, `MENTIONS` and
`RELATES_TO` relationships, `group_id` and `valid_at` on every node. So
the highest-value move is not to install a new temporal-graph library
against that store; it is to write episodic events in the shape that
schema already uses, and extend it with the crime-specific edges this
system actually needs.

**Access.** The live `mem0-neo4j` container is reachable on 127.0.0.1:7688
(bolt) and the `neo4j` driver is optional — this module degrades to
``None`` with a clear log line rather than raising, so an ingest pipeline
never dies because the graph is down.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any, Final, Protocol

logger = logging.getLogger(__name__)

# The only Neo4j in the fleet. `rmi-neo4j` does NOT exist — do not code
# against that name.
DEFAULT_BOLT_URI: Final = os.environ.get("RUGBRAIN_NEO4J_URI", "bolt://127.0.0.1:7688")
DEFAULT_GROUP: Final = os.environ.get("RUGBRAIN_GRAPH_GROUP", "rugbrain")


class Session(Protocol):
    """The slice of a neo4j driver session this package uses."""

    def run(self, query: str, **params: Any) -> Any: ...

    def __enter__(self) -> Session: ...

    def __exit__(self, *exc: Any) -> None: ...


class Driver(Protocol):
    def session(self, **kwargs: Any) -> Session: ...


def now_iso() -> str:
    """The single clock this package writes. Seconds precision, UTC."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def stable_key(prefix: str, value: str) -> str:
    """Namespaced, lowercased identity for a node.

    Stability is what makes re-ingest idempotent: digesting the same
    document twice must converge on the same node, not fork a second one.
    """
    return f"{prefix}:{value.strip().lower()}"


def build_driver(uri: str | None = None, password: str | None = None) -> Driver | None:
    """Return a Neo4j driver, or None when the driver is unavailable.

    Never raises. A missing driver is a normal state (graph enrichment is
    optional; the Qdrant write is the source of truth), so it is reported
    as None plus a log line rather than an exception.
    """
    try:
        from neo4j import GraphDatabase  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("neo4j driver not installed — graph layer disabled")
        return None
    try:
        return GraphDatabase.driver(  # type: ignore[no-any-return]
            uri or DEFAULT_BOLT_URI,
            auth=("neo4j", password or os.environ.get("RUGBRAIN_NEO4J_PASSWORD", "")),
        )
    except Exception as exc:  # pragma: no cover - driver construction rarely raises
        logger.warning("neo4j driver construction failed: %s", str(exc)[:120])
        return None
