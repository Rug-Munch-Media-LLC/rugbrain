"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Reference memory backend — process-local, dependency-free, fully testable.

Not production. It exists so the seam is exercisable and so a test does
not need a running mem0 or a running Neo4j. Swapping in mem0 is
implementing the same three methods of
:class:`~rugbrain.digest.memory_types.MemoryStore`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from rugbrain.digest.memory_types import Memory

_SEARCH_TERM_RE = re.compile(r"[a-z0-9]+")


class InMemoryMemoryStore:
    """Reference backend: process-local, no dependencies, fully testable."""

    def __init__(self) -> None:
        self._by_id: dict[str, Memory] = {}

    async def add(self, memory: Memory) -> str:
        stored = memory
        if not stored.created_at:
            stored = Memory(
                content=stored.content,
                namespace=stored.namespace,
                memory_id=stored.memory_id,
                source=stored.source,
                confidence=stored.confidence,
                created_at=datetime.now(UTC).isoformat(timespec="seconds"),
                metadata=stored.metadata,
            )
        self._by_id[stored.memory_id] = stored
        return stored.memory_id

    async def search(self, query: str, namespace: str, limit: int = 10) -> list[Memory]:
        cap = max(1, min(limit, 100))
        terms = [t for t in _SEARCH_TERM_RE.findall(query.lower()) if len(t) > 2]
        hits = [m for m in self._by_id.values() if m.namespace == namespace]
        if not terms:
            return hits[:cap]
        scored = [(sum(1 for t in terms if t in m.content.lower()), m) for m in hits]
        ranked = [(s, m) for s, m in scored if s > 0]
        ranked.sort(key=lambda pair: (-pair[0], pair[1].memory_id))
        return [m for _, m in ranked[:cap]]

    async def forget(self, memory_id: str) -> bool:
        return self._by_id.pop(memory_id, None) is not None
