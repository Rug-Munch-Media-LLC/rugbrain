"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Knowledge-graph layer over Neo4j — the graphiti-equivalent. Public surface.

This module is a **facade**. The responsibilities behind it each live in
one module with one job:

  * ``graph_driver`` — driver construction, node identity, the clock
  * ``graph_cypher``  — the queries, the rel allowlist, the rel renderer
  * ``graph_rows``   — EntitySet -> node rows (no extractor import)
  * ``graph_write``  — the episodic write; typed result, never raises
  * ``graph_read``   — the bounded entity -> episodes read

``from rugbrain.digest.graph import X`` keeps working for every existing
caller, including the private ``_rel_query`` the test suite asserts the
injection allowlist with, so the split is a package-internal detail
rather than a contract change.
"""

from __future__ import annotations

from rugbrain.digest.graph_cypher import (
    ALLOWED_RELS,
    REL_INVESTIGATES,
    REL_MENTIONS,
    REL_RELATES,
    REL_REPORTS_ON,
    rel_query,
)
from rugbrain.digest.graph_driver import (
    DEFAULT_BOLT_URI,
    DEFAULT_GROUP,
    Driver,
    Session,
    build_driver,
    now_iso,
    stable_key,
)
from rugbrain.digest.graph_read import find_entity_neighbors
from rugbrain.digest.graph_rows import entity_rows
from rugbrain.digest.graph_write import GraphWriteResult, write_episode

# The pre-split private names, kept because the test suite asserts the
# relationship allowlist through them. Aliases, not wrappers.
_rel_query = rel_query
_entity_rows = entity_rows
_now = now_iso
_stable_key = stable_key
_DEFAULT_BOLT_URI = DEFAULT_BOLT_URI
_DEFAULT_GROUP = DEFAULT_GROUP
_ALLOWED_RELS = ALLOWED_RELS

__all__ = [
    "ALLOWED_RELS",
    "DEFAULT_BOLT_URI",
    "DEFAULT_GROUP",
    "REL_INVESTIGATES",
    "REL_MENTIONS",
    "REL_RELATES",
    "REL_REPORTS_ON",
    "Driver",
    "GraphWriteResult",
    "Session",
    "build_driver",
    "entity_rows",
    "find_entity_neighbors",
    "rel_query",
    "stable_key",
    "write_episode",
]
