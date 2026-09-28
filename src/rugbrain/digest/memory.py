"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Persistent agent-memory seam, mem0-style.

**The fleet has no mem0.** Verified 2026-09-28: the `mem0-neo4j` container
is a bare `neo4j:5-community` image with no mem0 server, no API and no
adapter; there is no mem0 endpoint in the live opencode config. It holds
*corpus taxonomy* rows (Entity/CatalogEntry, repo catalogues), not agent
memories. So mem0 must be **built**, and this module is the seam that
decides where that build plugs in.

It is deliberately a **port**, not an implementation. ``MemoryStore`` is
the protocol; the backends are swappable. Why not just wire mem0 now:
the memory model is the part that is expensive to change later and cheap
to get right now, while the transport is cheap to swap. Fixing the
contract first means the eventual mem0 service is one adapter, not a
rewrite.

**The extraction problem.** A memory system that stores raw turns fills
up with noise. The insight: a *durable fact about a case* (this address is
a known drainer, this protocol was exploited on this date) is worth
persisting; a turn transcript is not. `should_remember` is that filter,
and it is deliberately strict — a false positive writes a memory that
will later be recalled as authoritative.

**Isolation.** Memories are namespaced by ``namespace`` (per agent, per
case, per tenant). A shared memory pool across agents is how one agent's
wrong conclusion becomes another's citation.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol, runtime_checkable

from rugbrain.digest.security import has_injection, scrub

logger = logging.getLogger(__name__)

# Facts worth carrying between sessions. Anchored so a statement about a
# *subject* is required — "X was drained by Y" is a fact; "check Y" is not.
_DURABLE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:was|were|is|has been|have been)\s+\w+ed\b",
        r"\bexploit(?:ed|ing)?\b",
        r"\bdrain(?:ed|s|ing)?\b",
        r"\brug(?:ged|pulled)?\b",
        r"\bscam(?:mer|med)?\b",
        r"\bphish(?:ing|ed)?\b",
        r"\blaunder(?:ed|ing)?\b",
        r"\bcompro mise?d\b|\bcompromised\b",
        r"\bblacklisted\b|\bwhitelisted\b",
        r"\b0x[a-fA-F0-9]{40}\b",
        r"\battacker\b|\bvictim\b|\bdeployer\b",
    )
)

# Explicitly transient: these are the shapes that make a memory system
# unusable if stored verbatim.
_TRANSIENT: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*(?:hi|hello|hey|thanks|thank you|ok|okay|yes|no)\s*[.!?]?\s*$",
        r"\bwhat (?:is|are) your (?:name|instructions|system prompt)\b",
        r"^\s*/[a-z]+\s*$",  # slash command echo
    )
)

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


def should_remember(
    text: str, *, namespace: str, source: str = "agent", min_confidence: float = 0.0
) -> tuple[bool, str]:
    """Return ``(keep, reason)``.

    Reasons are stable strings so a caller can log *why* a candidate was
    dropped, which is the only way to tune this without guessing.
    """
    stripped = text.strip()
    if not stripped:
        return False, "empty"
    if len(stripped) > MAX_MEMORY_CHARS:
        return False, "too_long"
    if any(p.search(stripped) for p in _TRANSIENT):
        return False, "transient"
    # A memory that carries an instruction is a stored prompt-injection
    # payload: it would be replayed verbatim into a future session.
    if has_injection(stripped):
        return False, "injection_shaped"
    if not any(p.search(stripped) for p in _DURABLE_PATTERNS):
        return False, "not_durable"
    if not namespace or not namespace.strip():
        return False, "no_namespace"
    _ = min_confidence
    return True, "ok"


class InMemoryMemoryStore:
    """Reference backend: process-local, no dependencies, fully testable.

    Not production. It exists so the seam is exercisable and so a test
    does not need a running mem0 or a running Neo4j. Swapping in mem0 is
    implementing the same three methods.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Memory] = {}
        self._seq = 0

    async def add(self, memory: Memory) -> str:
        self._seq += 1
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
        terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2]
        hits = [m for m in self._by_id.values() if m.namespace == namespace]
        if not terms:
            return hits[:cap]
        scored = [
            (sum(1 for t in terms if t in m.content.lower()), m)
            for m in hits
        ]
        scored = [(s, m) for s, m in scored if s > 0]
        scored.sort(key=lambda pair: (-pair[0], pair[1].memory_id))
        return [m for _, m in scored[:cap]]

    async def forget(self, memory_id: str) -> bool:
        return self._by_id.pop(memory_id, None) is not None


class MemoryManager:
    """Policy in front of a :class:`MemoryStore`.

    Callers never touch the store directly, so the remember/recall policy
    cannot be bypassed by a new integration.
    """

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
        """Persist *content* if it clears the durability and safety filters.

        Returns a result dict rather than raising: a memory that cannot be
        written is a degraded capability, not a failed request.
        """
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
