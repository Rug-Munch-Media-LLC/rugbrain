"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Write one episodic event and link its entities — never raises.

Graph enrichment failing must not fail an ingest, so every failure path
here returns a ``GraphWriteResult`` with ``ok=False`` and a stable error
string rather than propagating.
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.digest.graph_cypher import (
    ALLOWED_RELS,
    MERGE_ENTITY,
    NEO_FAIL,
    REL_MENTIONS,
    UPSERT_EPISODE,
    rel_query,
)
from rugbrain.digest.graph_driver import DEFAULT_GROUP, Driver, now_iso, stable_key
from rugbrain.digest.graph_result import GraphWriteResult
from rugbrain.digest.graph_rows import entity_rows

logger = logging.getLogger(__name__)


def _link_entities(session: Any, episode_key: str, rows: list[tuple], group_id: str) -> int:
    """Upsert each entity node and its MENTIONS edge. Returns the count."""
    for skey, ename, chain, kind in rows:
        session.run(
            MERGE_ENTITY,
            stable_key=skey,
            group_id=group_id,
            name=ename[:200],
            kind=kind,
            chain=chain,
            summary=None,
            labels=["Entity", kind.capitalize()],
        )
        session.run(
            rel_query(REL_MENTIONS),
            from_key=episode_key,
            to_key=skey,
            group_id=group_id,
        )
    return len(rows)


def _link_relationships(
    session: Any, relationships: tuple[tuple[str, str, str], ...], group_id: str
) -> int:
    """Write optional entity-to-entity edges. Unknown types are refused."""
    written = 0
    for from_key, to_key, rel_type in relationships:
        if not from_key or not to_key:
            continue
        if rel_type not in ALLOWED_RELS:
            # Interpolated into Cypher, so an unknown type is refused
            # rather than escaped.
            logger.warning("refused unknown relationship type %r", rel_type[:40])
            continue
        result = session.run(
            rel_query(rel_type), from_key=from_key, to_key=to_key, group_id=group_id
        )
        if result and result.single():
            written += 1
    return written


def write_episode(
    driver: Driver | None,
    *,
    stable_id: str,
    name: str,
    content: str,
    source: str,
    entities: Any,
    severity: str = "low",
    group_id: str = DEFAULT_GROUP,
    source_description: str = "",
    relationships: tuple[tuple[str, str, str], ...] = (),
) -> GraphWriteResult:
    """Write one episodic event and link its entities.

    *relationships* is an optional tuple of ``(from_stable_key,
    to_stable_key, rel_type)`` for entity-to-entity edges, e.g. a wallet
    that ``DEPLOYS`` a token. Omit it and you still get the episode plus
    its ``MENTIONS`` edges.

    Never raises. Graph enrichment failing must not fail an ingest.
    """
    if driver is None:
        return GraphWriteResult(ok=False, error="graph_disabled")
    if not stable_id or not content:
        return GraphWriteResult(ok=False, error="missing_required_field")

    rows = entity_rows(entities)
    episode_key = stable_key("episode", stable_id)
    try:
        with driver.session() as session:
            record = session.run(
                UPSERT_EPISODE,
                stable_key=episode_key,
                group_id=group_id,
                name=name[:200],
                content=content[:20_000],
                source=source,
                source_description=source_description,
                severity=severity,
                entities=[r[1] for r in rows],
                entity_edges=[r[0] for r in rows],
                now=now_iso(),
                extra_labels=["RugBrainEpisode"],
            )
            # Recording access forces the driver to materialise the row
            # inside the transaction; the uuid itself is not needed here.
            if record and record.single():
                record.single()

            linked = _link_entities(session, episode_key, rows, group_id)
            rels = _link_relationships(session, relationships, group_id)
            return GraphWriteResult(
                ok=True,
                nodes_created=linked,
                relationships_created=rels + linked,
                entities_linked=linked,
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("graph write failed: %s", str(exc)[:160])
        return GraphWriteResult(ok=False, error=NEO_FAIL)
