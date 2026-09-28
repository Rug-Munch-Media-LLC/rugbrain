"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
The memory contract — what a memory *is*, and the port a backend implements.

**The fleet has no mem0.** Verified 2026-09-28: the `mem0-neo4j` container
is a bare `neo4j:5-community` image with no mem0 server, no API and no
adapter; there is no mem0 endpoint in the live opencode config. It holds
*corpus taxonomy* rows, not agent memories. So mem0 must be **built**, and
this module is the seam that decides where that build plugs in.

It is deliberately a **port**, not an implementation. ``MemoryStore`` is
the protocol; the backends are swappable. Why not wire mem0 now: the
memory model is the part that is expensive to change later and cheap to
get right now, while the transport is cheap to swap. Fixing the contract
first means the eventual mem0 service is one adapter, not a rewrite.

**Isolation.** Memories are namespaced by ``namespace`` (per agent, per
case, per tenant). A shared memory pool across agents is how one agent's
wrong conclusion becomes another's citation.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

# Hard cap on a single memory. A memory is a compressed fact; a long one
# is a document that belongs in Qdrant, not in agent recall.
MAX_MEMORY_CHARS: Final = 1_200


@dataclass(frozen=True, slots=True)
class Memory:
    """One durable fact, with provenance.

    ``source`` and ``confidence`` are not decoration. A memory recalled
    without provenance is a rumour, and a crime RAG that cannot tell a
    confirmed on-chain fact from an agent's inference is worse than one
    that stores nothing.
    """

    content: str
    namespace: str
    memory_id: str
    source: str = "agent"
    confidence: float = 0.5
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.memory_id,
            "content": self.content,
            "namespace": self.namespace,
            "source": self.source,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@runtime_checkable
class MemoryStore(Protocol):
    """The port. Implement this to plug in mem0 (or anything else)."""

    async def add(self, memory: Memory) -> str: ...

    async def search(self, query: str, namespace: str, limit: int = 10) -> list[Memory]: ...

    async def forget(self, memory_id: str) -> bool: ...


def memory_id_for(content: str, namespace: str) -> str:
    """Deterministic id so re-remembering a fact updates rather than dupes."""
    digest = hashlib.sha256(f"{namespace}|{content.strip().lower()}".encode()).hexdigest()[:32]
    return str(uuid.UUID(digest))
