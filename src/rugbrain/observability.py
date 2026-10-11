"""
RAG Observability - Langfuse tracing + local metrics fallback.
Traces: embedding latency, retrieval latency, cache hit rate, ingest rate.
Langfuse primary, Redis metrics fallback when Langfuse unavailable.
"""

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("rag.observability")

LANGFUSE_AVAILABLE = False
try:
    from langfuse import Langfuse

    LANGFUSE_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    LANGFUSE_SECRET = os.getenv("LANGFUSE_SECRET_KEY", "")
    LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://rmi-langfuse:3000")
    if LANGFUSE_KEY and LANGFUSE_SECRET:
        _langfuse = Langfuse(
            public_key=LANGFUSE_KEY, secret_key=LANGFUSE_SECRET, host=LANGFUSE_HOST
        )
        LANGFUSE_AVAILABLE = True
        logger.info("Langfuse observability enabled")
    else:
        logger.info("Langfuse keys not set - using local metrics")
except ImportError:
    logger.info("Langfuse not installed - using local metrics")


@dataclass
class RAGMetrics:
    """Accumulated RAG metrics for local fallback."""

    embedding_calls: int = 0
    embedding_total_ms: float = 0
    retrieval_calls: int = 0
    retrieval_total_ms: float = 0
    cache_hits: int = 0
    cache_misses: int = 0
    ingest_docs: int = 0
    errors: int = 0


_metrics = RAGMetrics()


async def _save_local_metrics() -> None:
    """Persist metrics to Redis for dashboard."""
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )
        data = {
            "embedding_calls": _metrics.embedding_calls,
            "embedding_avg_ms": round(
                _metrics.embedding_total_ms / max(_metrics.embedding_calls, 1), 1
            ),
            "retrieval_calls": _metrics.retrieval_calls,
            "retrieval_avg_ms": round(
                _metrics.retrieval_total_ms / max(_metrics.retrieval_calls, 1), 1
            ),
            "cache_hits": _metrics.cache_hits,
            "cache_misses": _metrics.cache_misses,
            "cache_hit_rate": round(
                _metrics.cache_hits / max(_metrics.cache_hits + _metrics.cache_misses, 1), 3
            ),
            "ingest_docs": _metrics.ingest_docs,
            "errors": _metrics.errors,
        }
        await r.setex("rag:metrics", 3600, json.dumps(data))
        await r.close()
    except Exception:
        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)


def trace_embedding(
    duration_ms: float, dims: int = 1024, model: str = "qwen3-embedding-8b"
) -> None:
    """Record an embedding operation."""
    _metrics.embedding_calls += 1
    _metrics.embedding_total_ms += duration_ms
    if LANGFUSE_AVAILABLE:
        with suppress(Exception):
            _langfuse.trace(
                name="rag.embedding",
                metadata={"duration_ms": duration_ms, "dims": dims, "model": model},
            )


def trace_retrieval(
    duration_ms: float, collection: str, results: int, cache_hit: bool = False
) -> None:
    """Record a retrieval operation."""
    _metrics.retrieval_calls += 1
    _metrics.retrieval_total_ms += duration_ms
    if cache_hit:
        _metrics.cache_hits += 1
    else:
        _metrics.cache_misses += 1
    if LANGFUSE_AVAILABLE:
        with suppress(Exception):
            _langfuse.trace(
                name="rag.retrieval",
                metadata={
                    "duration_ms": duration_ms,
                    "collection": collection,
                    "results": results,
                    "cache_hit": cache_hit,
                },
            )


def trace_reranker(
    duration_ms: float, doc_count: int, top_k: int, model: str = "qwen3-reranker-8b"
) -> None:
    """Record a reranking operation."""
    if LANGFUSE_AVAILABLE:
        with suppress(Exception):
            _langfuse.trace(
                name="rag.reranker",
                metadata={
                    "duration_ms": duration_ms,
                    "doc_count": doc_count,
                    "top_k": top_k,
                    "model": model,
                },
            )


def trace_ingest(docs: int, collection: str) -> None:
    """Record an ingestion operation."""
    _metrics.ingest_docs += docs
    if LANGFUSE_AVAILABLE:
        with suppress(Exception):
            _langfuse.trace(
                name="rag.ingest",
                metadata={"docs": docs, "collection": collection},
            )


def trace_error(error_type: str, detail: str = "") -> None:
    """Record an error."""
    _metrics.errors += 1
    logger.warning("RAG error [%s]: %s", error_type, detail[:200])


@asynccontextmanager
async def timed_embedding(
    dims: int = 1024, model: str = "qwen3-embedding-8b"
) -> AsyncIterator[None]:
    """Context manager for timing embedding operations."""
    t0 = time.time()
    try:
        yield
    finally:
        trace_embedding((time.time() - t0) * 1000, dims, model)


@asynccontextmanager
async def timed_retrieval(collection: str, cache_hit: bool = False) -> AsyncIterator[None]:
    """Context manager for timing retrieval operations."""
    t0 = time.time()
    result_count = 0
    try:
        yield
    finally:
        trace_retrieval((time.time() - t0) * 1000, collection, result_count, cache_hit)


def get_metrics() -> dict[str, Any]:
    """Return current metrics snapshot."""
    return {
        "embedding": {
            "calls": _metrics.embedding_calls,
            "avg_ms": round(_metrics.embedding_total_ms / max(_metrics.embedding_calls, 1), 1),
        },
        "retrieval": {
            "calls": _metrics.retrieval_calls,
            "avg_ms": round(_metrics.retrieval_total_ms / max(_metrics.retrieval_calls, 1), 1),
        },
        "cache": {
            "hits": _metrics.cache_hits,
            "misses": _metrics.cache_misses,
            "hit_rate": round(
                _metrics.cache_hits / max(_metrics.cache_hits + _metrics.cache_misses, 1), 3
            ),
        },
        "ingestion": {"total_docs": _metrics.ingest_docs},
        "errors": _metrics.errors,
        "langfuse": LANGFUSE_AVAILABLE,
    }
