"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
The Cypher this package writes, and the allowlist that makes it safe.

**Temporal semantics.** Every edge is written with `valid_at` and
`invalidated_at` rather than deleted. A wallet that was a known scammer
in March and is not in June has *not* lost that fact — it has a fact
whose validity interval closed. A hard DELETE destroys the timeline that
makes a temporal graph worth building. This is the single most important
design decision in the package and the easiest one to get wrong.

**Relationship types.** Kept explicit so a typo fails at import rather
than silently creating a fourth kind of edge nobody queries.

**The injection boundary.** `rel_type` is a Cypher identifier and cannot
be a query parameter, so it is substituted from `_ALLOWED_RELS`. The
allowlist is the *entire* defence: an attacker-controlled string never
reaches the query text, so it cannot terminate the type and append a
second statement. The substitution is a literal token replacement, NOT
str.format — the query is full of Cypher `{}` braces and .format() would
try to interpret them.
"""

from __future__ import annotations

from typing import Final

REL_MENTIONS: Final = "MENTIONS"  # (Episodic)-[:MENTIONS]->(Entity)
REL_RELATES: Final = "RELATES_TO"  # (Entity)-[:RELATES_TO]->(Entity)
REL_INVESTIGATES: Final = "INVESTIGATES"  # (Episodic)-[:INVESTIGATES]->(Entity)
REL_REPORTS_ON: Final = "REPORTS_ON"  # (Episodic)-[:REPORTS_ON]->(Entity)

ALLOWED_RELS: Final = frozenset({REL_MENTIONS, REL_RELATES, REL_INVESTIGATES, REL_REPORTS_ON})

# A Neo4j bolt error that must not propagate out of an ingest path.
NEO_FAIL: Final = "Neo4jUnavailable"

# Idempotent create. `ON CREATE SET` vs `ON MATCH SET` is what makes a
# second digest of the same document an update, not a duplicate.
UPSERT_EPISODE: Final = """
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

MERGE_ENTITY: Final = """
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

MERGE_REL: Final = """
MATCH (a {stable_key: $from_key, group_id: $group_id})
MATCH (b {stable_key: $to_key,   group_id: $group_id})
MERGE (a)-[r:__REL_TYPE__]->(b)
ON CREATE SET r.uuid = randomUUID(), r.created_at = datetime($now)
SET r.valid_at = coalesce(r.valid_at, datetime($now))
RETURN r.uuid AS uuid
"""

# S105: not a credential — a Cypher placeholder token.
_REL_TOKEN: Final = "__REL_TYPE__"  # noqa: S105

# (Episodic)-[:MENTIONS]-(Entity), newest first, for entity -> episodes.
NEIGHBOR_EPISODES: Final = """
MATCH (n:Entity {stable_key: $key, group_id: $group_id})<-[:MENTIONS]-(e:Episodic)
RETURN e.uuid AS uuid, e.name AS name, e.source AS source,
       e.severity AS severity, e.valid_at AS valid_at
ORDER BY e.valid_at DESC
LIMIT $limit
"""


def rel_query(rel_type: str) -> str:
    """Render a MERGE query for an allowlisted relationship type.

    Raises ``ValueError`` for anything not in the allowlist — see the
    module docstring for why that allowlist is the injection defence.
    """
    if rel_type not in ALLOWED_RELS:
        raise ValueError(f"relationship type not allowed: {rel_type[:40]!r}")
    return MERGE_REL.replace(_REL_TOKEN, rel_type)
