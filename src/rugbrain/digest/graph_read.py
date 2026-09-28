"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Read side of the graph: which episodes mention a given entity.

Separate from the write side because the two have opposite failure
contracts — a write reports a typed result, a read returns an empty list
— and because only the read path is unbounded enough to need its own cap.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from rugbrain.digest.graph_cypher import NEIGHBOR_EPISODES
from rugbrain.digest.graph_driver import DEFAULT_GROUP, Driver, stable_key

logger = logging.getLogger(__name__)

# The graph reader walks out of a hot entity node, so it is hard-capped.
MAX_NEIGHBORS: Final = 100

# A caller names the kind it has ("address"); the graph is keyed by the
# per-chain prefix. An unknown kind is used verbatim as its own prefix.
_KIND_PREFIXES: Final[dict[str, str]] = {
    "address": "evm",
    "domain": "domain",
    "token": "token",
    "protocol": "protocol",
    "chain": "chain",
}


def find_entity_neighbors(
    driver: Driver | None,
    *,
    stable_id: str,
    entity_kind: str,
    group_id: str = DEFAULT_GROUP,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Return the episodes mentioning an entity, newest first.

    Bounded by *limit* (capped at 100) because this walks out of a hot
    entity node — a scammer address mentioned in 40,000 episodes is a
    real row count, not a hypothetical one.
    """
    if driver is None:
        return []
    try:
        with driver.session() as session:
            result = session.run(
                NEIGHBOR_EPISODES,
                key=stable_key(_KIND_PREFIXES.get(entity_kind, entity_kind), stable_id),
                group_id=group_id,
                limit=max(1, min(limit, MAX_NEIGHBORS)),
            )
            return [dict(rec) for rec in result]
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("graph read failed: %s", str(exc)[:160])
        return []
