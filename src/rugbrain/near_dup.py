"""Near-duplicate detection — in-process, fast-embedder backed.

For high-throughput ingestion streams (firehose, telegram firehose, RSS)
where a Qdrant round-trip per item is too heavy. Maintains a sliding
window of recent text embeddings (fast embedder — both sides of every
comparison run on the same model, honoring the cross-model rule) and
flags texts whose max cosine similarity against the window exceeds the
threshold.

Design:
* asyncio-safe (single event loop, no locks needed for dict ops between
  awaits; the embed await is the only yield point)
* TTL + max-size eviction keeps memory bounded
* fail-open: embedder failure → (False, 0.0) — never blocks ingestion
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any

log = logging.getLogger("rugbrain.near_dup")

_DEFAULT_THRESHOLD = 0.92
_DEFAULT_WINDOW = 2048
_DEFAULT_TTL_S = 3600.0


class NearDuplicateIndex:
    """Sliding-window cosine index over fast-embedder vectors."""

    def __init__(
        self,
        embedder: Any,
        threshold: float = _DEFAULT_THRESHOLD,
        window: int = _DEFAULT_WINDOW,
        ttl_s: float = _DEFAULT_TTL_S,
    ):
        self._embedder = embedder
        self.threshold = threshold
        self.window = window
        self.ttl_s = ttl_s
        # key -> (embedding, inserted_at)
        self._store: OrderedDict[str, tuple[list[float], float]] = OrderedDict()
        self.duplicates_seen = 0

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)  # type: ignore[no-any-return]  # sum() over generator is Any

    def _evict(self, now: float) -> None:
        cutoff = now - self.ttl_s
        while self._store:
            _, (_, inserted) = next(iter(self._store.items()))
            if inserted > cutoff:
                break
            self._store.popitem(last=False)
        # reserve a slot for the incoming entry (eviction precedes insert)
        while len(self._store) >= self.window:
            self._store.popitem(last=False)

    async def check_and_add(self, key: str, text: str) -> tuple[bool, float]:
        """Check *text* against the window; add it if not a duplicate.

        Returns ``(is_duplicate, max_similarity)``. Embedder failure is
        fail-open: returns (False, 0.0) without adding.
        """
        try:
            vec = await self._embedder.embed_one(text)
        except Exception as exc:
            log.debug("near_dup_embed_failed key=%s err=%s", key[:20], exc)
            return False, 0.0
        if not vec:
            return False, 0.0

        now = time.monotonic()
        self._evict(now)

        max_sim = 0.0
        for existing_vec, _ts in self._store.values():
            sim = self._cosine(vec, existing_vec)
            if sim > max_sim:
                max_sim = sim

        if max_sim >= self.threshold:
            self.duplicates_seen += 1
            return True, round(max_sim, 4)

        self._store[key] = (vec, now)
        self._store.move_to_end(key)
        return False, round(max_sim, 4)

    def stats(self) -> dict[str, Any]:
        return {
            "window_used": len(self._store),
            "window_max": self.window,
            "threshold": self.threshold,
            "duplicates_seen": self.duplicates_seen,
        }
