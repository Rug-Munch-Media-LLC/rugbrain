"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Turn an EntitySet-shaped value into graph node rows.

The graph layer deliberately does not import the extractor. It accepts
any mapping with an ``as_dict()`` (or a plain dict), which keeps the
graph package independently testable and stops the two packages from
growing a circular dependency.
"""

from __future__ import annotations

from typing import Any

from rugbrain.digest.graph_driver import stable_key

# (stable_key, name, chain, kind) per entity group. Chain is only set
# where it is genuinely known — a domain or a token has no chain.
_ROW_SOURCES: tuple[tuple[str, str, str, str], ...] = (
    ("evm_addresses", "evm", "ethereum", "address"),
    ("solana_addresses", "solana", "solana", "address"),
    ("tron_addresses", "tron", "tron", "address"),
    ("domains", "domain", "", "domain"),
    ("tickers", "token", "", "token"),
    ("protocols", "protocol", "", "protocol"),
    ("chains", "chain", "", "chain"),
)


def entity_rows(entities: Any) -> list[tuple[str, str, str | None, str]]:
    """Normalize an ``EntitySet``-shaped mapping into graph rows.

    Returns ``(stable_key, name, chain, kind)`` tuples. Accepts the dict
    form from ``EntitySet.as_dict()`` so the digest layer does not have to
    import the extractor.
    """
    data = entities.as_dict() if hasattr(entities, "as_dict") else dict(entities)
    rows: list[tuple[str, str, str | None, str]] = []
    for field, prefix, chain, kind in _ROW_SOURCES:
        for name in data.get(field, []):
            # "" is the "unknown" chain, not the empty string; Neo4j
            # would otherwise get a chain of "" instead of no chain.
            rows.append((stable_key(prefix, name), name, chain or None, kind))
    return rows
