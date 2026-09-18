"""BM25 sparse embeddings (fastembed) for hybrid Qdrant search."""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# ── BM25 sparse embeddings (fastembed) ──────────────────────────────

# Name of the sparse vector in hybrid collections. Qdrant collection
# configs are immutable, so sparse support is provisioned by creating
# ``{name}_v2`` collections (see scripts/migrate_to_sparse.py) and
# pointing the original name at them via an alias.
SPARSE_VECTOR_NAME = "bm25"
# Name of the dense vector in hybrid collections (must match the config
# used by scripts/migrate_to_sparse.py when creating ``{name}_v2``).
DENSE_VECTOR_NAME = "dense"

_bm25_embedder: Any = None
_bm25_embedder_lock = threading.Lock()


def _get_bm25_embedder() -> Any | None:
    """Lazily load the fastembed ``Qdrant/bm25`` sparse embedder.

    Returns None when fastembed is unavailable or the model fails to
    load so callers can degrade to dense-only behaviour instead of
    crashing the ingest/search path.
    """
    global _bm25_embedder
    if _bm25_embedder is not None:
        return _bm25_embedder if _bm25_embedder is not False else None
    with _bm25_embedder_lock:
        try:
            from fastembed import SparseTextEmbedding

            _bm25_embedder = SparseTextEmbedding(model_name="Qdrant/bm25")
            logger.info("bm25_embedder_loaded model=Qdrant/bm25")
        except Exception as e:
            logger.warning("bm25_embedder_unavailable err=%s", e)
            _bm25_embedder = False
    return _bm25_embedder if _bm25_embedder is not False else None


async def _embed_sparse(text: str) -> tuple[list[int], list[float]] | None:
    """Compute a BM25 sparse vector ``(indices, values)`` for *text*.

    Returns None when *text* is empty or the BM25 embedder is not
    available (callers fall back to dense-only vectors).
    """
    if not text or not text.strip():
        return None
    embedder = _get_bm25_embedder()
    if embedder is None:
        return None
    try:
        sparse = next(iter(embedder.embed([text])))
        return (sparse.indices.tolist(), [float(v) for v in sparse.values.tolist()])
    except Exception as e:
        logger.warning("bm25_embed_error: %s", e)
        return None
