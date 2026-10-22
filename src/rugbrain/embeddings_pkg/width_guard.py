"""The one place a lane's vector WIDTH is accepted or refused.

The corpus is a single 4096-dim space (Qwen3-Embedding-8B). A 1024-dim vector
is supermemory's 0.6B lane on ``:8004``; 2048 and 2560 are retired generations.
None of them may be written to a 4096 collection, and a provider that silently
changes width must not degrade retrieval — it must be skipped, loudly.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def accepts_vector(vector: list[float] | None, want: int, lane: str) -> bool:
    """True only for a vector of exactly ``want`` floats; logs every rejection."""
    if vector is None:
        return False
    if len(vector) == want:
        return True
    log.error("embed_lane_dim_mismatch lane=%s got=%d want=%d", lane, len(vector), want)
    return False
