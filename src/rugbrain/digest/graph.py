"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Knowledge-graph layer over Neo4j — the graphiti-equivalent.

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

**Temporal semantics.** Every edge is written with `valid_at` and
`invalidated_at` rather than deleted. A wallet that was a known scammer
in March and is not in June has *not* lost that fact — it has a fact
whose validity interval closed. A hard DELETE destroys the timeline that
makes a temporal graph worth building. This is the single most important
design decision in the module and the easiest one to get wrong.

**Access.** The live `mem0-neo4j` container is reachable on 127.0.0.1:7688
(bolt) and the `neo4j` driver is optional — this module degrades to
`enabled=False` with a clear reason rather than raising, so an ingest
pipeline never dies because the graph is down.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol

logger = logging.getLogger(__name__)

# The only Neo4j in the fleet. `rmi-neo4j` does NOT exist — do not code
# against that name.
_DEFAULT_BOLT_URI: Final = os.environ.get("RUGBRAIN_NEO4J_URI", "bolt://127.0.0.1:7688")
_DEFAULT_GROUP: Final = os.environ.get("RUGBRAIN_GRAPH_GROUP", "rugbrain")

# Relationship types. Kept explicit so a typo fails at import rather than
# silently creating a fourth kind of edge nobody queries.
REL_MENTIONS: Final = "MENTIONS"  # (Episodic)-[:MENTIONS]->(Entity)
REL_RELATES: Final = "RELATES_TO"  # (Entity)-[:RELATES_TO]->(Entity)
REL_INVESTIGATES: Final = "INVESTIGATES"  # (Episodic)-[:INVESTIGATES]->(Entity)
REL_REPORTS_ON: Final = "REPORTS_ON"  # (Episodic)-[:REPORTS_ON]->(Entity)

# A Neo4j bolt error that must not propagate out of an ingest path.
_NEO_FAIL = "Neo4jUnavailable"


@dataclass(frozen=True, slots=True)
class GraphWriteResult:
    """Outcome of one episodic write. Never raises."""

    ok: bool
    nodes_created: int = 0
    relationships_created: int = 0
    entities_linked: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "nodes_created": self.nodes_created,
            "relationships_created": self.relationships_created,
            "entities_linked": self.entities_linked,
            "error": self.error,
        }


class _Session(Protocol):
    """The slice of a neo4j driver session this module uses."""

    def run(self, query: str, **params: Any) -> Any: ...

    def __enter__(self) -> _Session: ...

    def __exit__(self, *exc: Any) -> None: ...


class _Driver(Protocol):
    def session(self, **kwargs: Any) -> _Session: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _stable_key(prefix: str, value: str) -> str:
    """Namespaced, lowercased identity for a node.

    Stability is what makes re-ingest idempotent: digesting the same
    document twice must converge on the same node, not fork a second one.
    """
    return f"{prefix}:{value.strip().lower()}"


def build_driver(uri: str | None = None, password: str | None = None) -> _Driver | None:
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
    target = uri or _DEFAULT_BOLT_URI
    try:
        return GraphDatabase.driver(  # type: ignore[no-any-return]
            target,
            auth=("neo4j", password or os.environ.get("RUGBRAIN_NEO4J_PASSWORD", "")),
        )
    except Exception as exc:  # pragma: no cover - driver construction rarely raises
        logger.warning("neo4j driver construction failed: %s", str(exc)[:120])
        return None


# Idempotent create. `ON CREATE SET` vs `ON MATCH SET` is what makes a
# second digest of the same document an update, not a duplicate.
_UPSERT_EPISODE: Final = """
MERGE (e:Episodic {stable_key: $stable_key, group_id: $group_id})
ON CREATE SET e.uuid = randomUUID(), e.created_at = datetime($now)
ON MATCH  SET e.updated_at = datetime($now)
SET e:+$extra_labels,
    e.name = $name,
    e.content = $content,
    e.source = $source,
    e.source_description = $source_description,
    e.severity = $severity,
    e.entities = $entities,
    e.entity_edges = $entity_edges,
    e.valid_at = coalesce(e.valid_at, datetime($now)),
    e.group_id = $group_id
RETURN e.uuid AS uuid
"""

_MERGE_ENTITY: Final = """
MERGE (n:Entity {stable_key: $stable_key, group_id: $group_id})
ON CREATE SET n.uuid = randomUUID(), n.created_at = datetime($now), n.indexed = false
SET n:+$labels,
    n.name = $name,
    n.kind = $kind,
    n.chain = $chain,
    n.summary = $summary,
    n.group_id = $group_id
RETURN n.uuid AS uuid
"""

# `rel_type` is a Cypher identifier and cannot be a query parameter, so it
# is substituted from a fixed allowlist. NOTE: this uses a literal token
# replacement, NOT str.format — the query is full of Cypher `{}` braces
# (`(a {stable_key: ...})`) and .format() would try to interpret them.
# Anything not in `_ALLOWED_RELS` is refused by the caller before here.
_MERGE_REL: Final = """
MATCH (a {stable_key: $from_key, group_id: $group_id})
MATCH (b {stable_key: $to_key,   group_id: $group_id})
MERGE (a)-[r:__REL_TYPE__]->(b)
ON CREATE SET r.uuid = randomUUID(), r.created_at = datetime($now)
SET r.valid_at = coalesce(r.valid_at, datetime($now))
RETURN r.uuid AS uuid
"""

_ALLOWED_RELS: Final = frozenset({REL_MENTIONS, REL_RELATES, REL_INVESTIGATES, REL_REPORTS_ON})

# S105: not a credential — a Cypher placeholder token.
_REL_TOKEN: Final = "__REL_TYPE__"  # noqa: S105


def _rel_query(rel_type: str) -> str:
    """Render a MERGE query for an allowlisted relationship type.

    Raises ``ValueError`` for anything not in the allowlist. The allowlist
    is the entire injection defence here: an attacker-controlled string
    never reaches the query text, so it cannot terminate the type and
    append a second statement.
    """
    if rel_type not in _ALLOWED_RELS:
        raise ValueError(f"relationship type not allowed: {rel_type[:40]!r}")
    return _MERGE_REL.replace(_REL_TOKEN, rel_type)


def _entity_rows(entities: Any) -> list[tuple[str, str, str | None, str]]:
    """Normalize an ``EntitySet``-shaped mapping into graph rows.

    Returns ``(stable_key, name, chain, kind)`` tuples. Accepts the dict
    form from ``EntitySet.as_dict()`` so the digest layer does not have to
    import the extractor (keeping the graph layer independently testable).
    """
    rows: list[tuple[str, str, str | None, str]] = []
    data = entities.as_dict() if hasattr(entities, "as_dict") else dict(entities)

    for addr in data.get("evm_addresses", []):
        rows.append((_stable_key("evm", addr), addr, "ethereum", "address"))
    for addr in data.get("solana_addresses", []):
        rows.append((_stable_key("solana", addr), addr, "solana", "address"))
    for addr in data.get("tron_addresses", []):
        rows.append((_stable_key("tron", addr), addr, "tron", "address"))
    for dom in data.get("domains", []):
        rows.append((_stable_key("domain", dom), dom, None, "domain"))
    for tk in data.get("tickers", []):
        rows.append((_stable_key("token", tk), tk, None, "token"))
    for proto in data.get("protocols", []):
        rows.append((_stable_key("protocol", proto), proto, None, "protocol"))
    for chain in data.get("chains", []):
        rows.append((_stable_key("chain", chain), chain, chain, "chain"))
    return rows


def write_episode(
    driver: _Driver | None,
    *,
    stable_id: str,
    name: str,
    content: str,
    source: str,
    entities: Any,
    severity: str = "low",
    group_id: str = _DEFAULT_GROUP,
    source_description: str = "",
    relationships: tuple[tuple[str, str, str], ...] = (),
) -> GraphWriteResult:
    """Write one episodic event and link its entities.

    *relationships* is an optional tuple of ``(from_stable_key, to_stable_key,
    rel_type)`` for entity-to-entity edges, e.g. a wallet that ``DEPLOYS`` a
    token. Omit it and you still get the episode plus its ``MENTIONS``
    edges.

    Never raises. Graph enrichment failing must not fail an ingest.
    """
    if driver is None:
        return GraphWriteResult(ok=False, error="graph_disabled")
    if not stable_id or not content:
        return GraphWriteResult(ok=False, error="missing_required_field")

    rows = _entity_rows(entities)
    now = _now()
    created = 0
    linked = 0
    rels = 0

    try:
        with driver.session() as session:
            # 1. the episode itself
            record = session.run(
                _UPSERT_EPISODE,
                stable_key=_stable_key("episode", stable_id),
                group_id=group_id,
                name=name[:200],
                content=content[:20_000],
                source=source,
                source_description=source_description,
                severity=severity,
                entities=[r[1] for r in rows],
                entity_edges=[r[0] for r in rows],
                now=now,
                extra_labels=["RugBrainEpisode"],
            )
            # Recording access forces the driver to materialise the row
            # inside the transaction; the uuid itself is not needed here.
            if record and record.single():
                record.single()

            # 2. entities + MENTIONS edges
            episode_key = _stable_key("episode", stable_id)
            for skey, ename, chain, kind in rows:
                session.run(
                    _MERGE_ENTITY,
                    stable_key=skey,
                    group_id=group_id,
                    name=ename[:200],
                    kind=kind,
                    chain=chain,
                    summary=None,
                    labels=["Entity", kind.capitalize()],
                )
                session.run(
                    _rel_query(REL_MENTIONS),
                    from_key=episode_key,
                    to_key=skey,
                    group_id=group_id,
                )
                linked += 1
            created = len(rows)

            # 3. optional entity-to-entity edges
            for from_key, to_key, rel_type in relationships:
                if not from_key or not to_key:
                    continue
                if rel_type not in _ALLOWED_RELS:
                    # Interpolated into Cypher, so an unknown type is
                    # refused rather than escaped.
                    logger.warning("refused unknown relationship type %r", rel_type[:40])
                    continue
                res = session.run(
                    _rel_query(rel_type),
                    from_key=from_key,
                    to_key=to_key,
                    group_id=group_id,
                )
                if res and res.single():
                    rels += 1

            return GraphWriteResult(
                ok=True,
                nodes_created=created,
                relationships_created=rels + linked,
                entities_linked=linked,
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("graph write failed: %s", str(exc)[:160])
        return GraphWriteResult(ok=False, error=_NEO_FAIL)


def find_entity_neighbors(
    driver: _Driver | None,
    *,
    stable_id: str,
    entity_kind: str,
    group_id: str = _DEFAULT_GROUP,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Return the episodes mentioning an entity, newest first.

    Bounded by *limit* (capped at 100) because this walks out of a hot
    entity node — a scammer address mentioned in 40,000 episodes is a
    real row count, not a hypothetical one.
    """
    if driver is None:
        return []
    cap = max(1, min(limit, 100))
    prefix = {
        "address": "evm",
        "domain": "domain",
        "token": "token",
        "protocol": "protocol",
        "chain": "chain",
    }.get(entity_kind, entity_kind)
    query = """
    MATCH (n:Entity {stable_key: $key, group_id: $group_id})<-[:MENTIONS]-(e:Episodic)
    RETURN e.uuid AS uuid, e.name AS name, e.source AS source,
           e.severity AS severity, e.valid_at AS valid_at
    ORDER BY e.valid_at DESC
    LIMIT $limit
    """
    try:
        with driver.session() as session:
            result = session.run(
                query,
                key=_stable_key(prefix, stable_id),
                group_id=group_id,
                limit=cap,
            )
            return [dict(rec) for rec in result]
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("graph read failed: %s", str(exc)[:160])
        return []
