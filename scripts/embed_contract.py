"""The corpus embedding contract: Qwen3-Embedding-8B, 4096-d, nothing else.

Lives on its own so every lane, store and test imports ONE definition of the
contract instead of re-deriving it. See ``rugbrain/embedding_config.py`` for
the collection side and ``/etc/rugbrain/rag-rollback.env`` for the generation.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("rugbrain.embed_contract")

CORPUS_DIM = int(os.getenv("QWEN_EMBED_DIMS") or os.getenv("RAG_EMBED_DIM") or "4096")
MAX_CHARS = int(os.getenv("QWEN3_EMBED_MAX_CHARS", "8000"))

# Ports that serve a DIFFERENT vector space: :8004 is supermemory's
# Qwen3-Embedding-0.6B / 1024-d lane. Corpus traffic there yields vectors the
# store must reject and an ingest that looks successful while storing nothing.
NON_CORPUS_PORTS = frozenset(int(p) for p in os.getenv("RUG_EMBED_NON_CORPUS_PORTS", "8004").split(",") if p)


class EmbedLaneExhausted(RuntimeError):
    """No 4096-d lane produced vectors. Callers MUST NOT treat this as success."""


def port_of(url: str) -> int:
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).port or 0
    except ValueError:
        return 0


def validate_dimensions(items: list[dict], want: int = CORPUS_DIM) -> list[list[float]] | None:
    """Return vectors only when EVERY entry is the corpus width, else None."""
    out: list[list[float]] = []
    for item in items:
        vec = item.get("embedding")
        if not isinstance(vec, list) or len(vec) != want:
            logger.error(
                "embed_dim_contract_violation got=%s want=%d (lane refused)",
                len(vec) if isinstance(vec, list) else type(vec).__name__,
                want,
            )
            return None
        out.append([float(x) for x in vec])
    return out
