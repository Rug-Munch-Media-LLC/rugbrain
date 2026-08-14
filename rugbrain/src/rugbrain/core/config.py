"""RAG pipeline configuration constants.

Centralised configuration for the continuous ingestion pipeline and retrieval.
All tunable knobs live here, not scattered across modules.

Port of the private ``app/rag/config.py`` with every knob sourced from
:mod:`rugbrain.settings` (env-driven, ``RAG_`` prefix) instead of hardcoded
module constants, and with neutral collection defaults. Collection names are
deployment configuration — point ``RAG_COLLECTIONS`` at your own namespaces.

Usage:
    from rugbrain.core.config import (
        RAG_INGESTION_BATCH_SIZE,
        RAG_INGESTION_MAX_PER_MINUTE,
        RAG_CHUNK_SIZE,
        RAG_CHUNK_OVERLAP,
        RAG_COLLECTIONS,
    )
"""

from __future__ import annotations

from rugbrain.settings import settings

# ── Ingestion pipeline ────────────────────────────────────────────────

# Maximum documents per batch before flushing to the embedding store.
RAG_INGESTION_BATCH_SIZE: int = settings.ingestion_batch_size

# Hard cap on ingestions per minute (across all collections).
RAG_INGESTION_MAX_PER_MINUTE: int = settings.ingestion_max_per_minute

# ── Chunking ──────────────────────────────────────────────────────────

# Target chunk size in characters (512 chars ≈ 128–200 tokens for most text).
RAG_CHUNK_SIZE: int = settings.chunk_size

# Overlap between consecutive chunks to preserve cross-chunk context.
RAG_CHUNK_OVERLAP: int = settings.chunk_overlap

# Minimum chunk length in characters. Shorter chunks are discarded as noise.
RAG_MIN_CHUNK_CHARS: int = settings.min_chunk_chars

# ── Collections ───────────────────────────────────────────────────────

# All active RAG collections. Each is a separate vector index + keyword
# store. Neutral defaults — replace with deployment config via env
# (``RAG_COLLECTIONS``).
RAG_COLLECTIONS: list[str] = list(settings.collections or ["docs"])

# ── Quality filter ────────────────────────────────────────────────────

# Minimum content length (characters) for a document to be accepted.
RAG_MIN_DOC_LENGTH: int = settings.min_doc_length

# Maximum spam-like repetition ratio (e.g. 0.3 = 30% repeated n-grams).
RAG_SPAM_REPETITION_THRESHOLD: float = settings.spam_repetition_threshold

# ── Query expansion ───────────────────────────────────────────────────

# Number of additional query terms to generate during expansion.
RAG_EXPANSION_TERMS: int = settings.expansion_terms

# Weight for vector similarity vs. keyword overlap in hybrid retrieval.
# 1.0 = pure vector; 0.0 = pure keyword.
RAG_HYBRID_ALPHA: float = settings.hybrid_alpha

# ── Relevance scoring ─────────────────────────────────────────────────

# RRF constant for reciprocal rank fusion (k=60 is the standard).
RAG_RRF_K: int = settings.rrf_k

# ── Context budget ────────────────────────────────────────────────────
# Hard token budget for the context handed to the LLM. Applied AFTER
# cross-encoder rerank on the primary search facade. Env override:
# ``RAG_CONTEXT_MAX_TOKENS``.
RAG_CONTEXT_MAX_TOKENS: int = settings.context_max_tokens

# ── RAG regression eval ───────────────────────────────────────────────
# Minimum aggregate metrics for the CI gate. Env overrides:
# ``RAG_EVAL_HIT_RATE_MIN``, ``RAG_EVAL_FAITHFULNESS_MIN``.
RAG_EVAL_HIT_RATE_MIN: float = settings.eval_hit_rate_min
RAG_EVAL_FAITHFULNESS_MIN: float = settings.eval_faithfulness_min

# ── Ingestion scheduler ───────────────────────────────────────────────

# Default interval (seconds) between scheduled ingestion sweeps.
RAG_SCHEDULER_INTERVAL: int = settings.scheduler_interval

__all__ = [
    "RAG_CHUNK_OVERLAP",
    "RAG_CHUNK_SIZE",
    "RAG_COLLECTIONS",
    "RAG_CONTEXT_MAX_TOKENS",
    "RAG_EVAL_FAITHFULNESS_MIN",
    "RAG_EVAL_HIT_RATE_MIN",
    "RAG_EXPANSION_TERMS",
    "RAG_HYBRID_ALPHA",
    "RAG_INGESTION_BATCH_SIZE",
    "RAG_INGESTION_MAX_PER_MINUTE",
    "RAG_MIN_CHUNK_CHARS",
    "RAG_MIN_DOC_LENGTH",
    "RAG_RRF_K",
    "RAG_SCHEDULER_INTERVAL",
    "RAG_SPAM_REPETITION_THRESHOLD",
]
