"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Policy in front of a MemoryStore — the only sanctioned way to call it.

Callers never touch the store directly, so the remember/recall policy
cannot be bypassed by a new integration. Every failure mode is a returned
dict, never an exception: a memory that cannot be written is a degraded
capability, not a failed request.
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.digest.memory_filter import should_remember
from rugbrain.digest.memory_local import InMemoryMemoryStore
from rugbrain.digest.memory_types import (
    MAX_MEMORY_CHARS,
    Memory,
    MemoryStore,
    memory_id_for,
)
from rugbrain.digest.security import scrub

logger = logging.getLogger(__name__)


class MemoryManager:
    """Policy in front of a :class:`MemoryStore`."""

    def __init__(self, store: MemoryStore | None = None) -> None:
        self.store: MemoryStore = store or InMemoryMemoryStore()

    async def remember(
        self,
        content: str,
        *,
        namespace: str,
        source: str = "agent",
        confidence: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist *content* if it clears the durability and safety filters."""
        keep, reason = should_remember(content, namespace=namespace, source=source)
        if not keep:
            return {"ok": False, "stored": False, "reason": reason, "id": None}

        clean = scrub(content)[:MAX_MEMORY_CHARS]
        memory = Memory(
            content=clean,
            namespace=namespace.strip(),
            memory_id=memory_id_for(clean, namespace),
            source=source,
            confidence=confidence,
            metadata=metadata or {},
        )
        try:
            memory_id = await self.store.add(memory)
        except Exception as exc:  # pragma: no cover - backend failure
            logger.warning("memory store add failed: %s", str(exc)[:140])
            return {"ok": False, "stored": False, "reason": "store_unavailable", "id": None}
        return {"ok": True, "stored": True, "reason": reason, "id": memory_id}

    async def recall(self, query: str, *, namespace: str, limit: int = 10) -> list[dict[str, Any]]:
        """Recall memories for *namespace*.

        Queries are scrubbed on the way in and results on the way out, so
        a poisoned memory cannot be replayed verbatim into the caller's
        context and a secret cannot be echoed back.
        """
        if not namespace.strip():
            return []
        try:
            hits = await self.store.search(scrub(query), namespace, limit=limit)
        except Exception as exc:  # pragma: no cover - backend failure
            logger.warning("memory store search failed: %s", str(exc)[:140])
            return []
        return [h.as_dict() for h in hits]

    async def forget(self, memory_id: str) -> bool:
        try:
            return await self.store.forget(memory_id)
        except Exception as exc:  # pragma: no cover - backend failure
            logger.warning("memory store forget failed: %s", str(exc)[:140])
            return False
