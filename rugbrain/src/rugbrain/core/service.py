"""RAG service - HTTP facade over the v3 RAG engine.

Provides a clean async Pydantic surface for search, ingest, feedback.
Delegates to the M3 engine in app.rag.engine (built per v3 unfuck plan).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from rugbrain._logging import get_logger
from rugbrain.core.engine import (
    get_stats as engine_get_stats,
)
from rugbrain.core.engine import (
    ingest_document as engine_ingest_document,
)
from rugbrain.core.engine import (
    three_pillar_search as engine_three_pillar_search,
)
from rugbrain.core.models import (
    FeedbackRecord,
    IngestRequest,
    IngestResult,
    SearchHit,
    SearchRequest,
    SearchResponse,
)

log = get_logger(__name__)


def _record_rag_proxy(operation: str, status: str) -> None:
    """Record a backend->RAG proxy call.

    The private monorepo incremented a Prometheus counter here
    (``app.core.metrics.RAG_BACKEND_PROXY``). In the standalone package this
    is a no-op hook — host applications can patch it to forward metrics.
    """
    log.debug("rag_proxy", operation=operation, status=status)


class RAGService:
    """Async facade over the v3 RAG engine (M3 deliverable)."""

    async def search(self, req: SearchRequest) -> SearchResponse:
        """Search the RAG system. Returns a Pydantic response."""
        log.info(
            "rag_search_started",
            collection=req.collection,
            top_k=req.top_k,
            query_len=len(req.query),
        )
        start = time.monotonic()
        # Expand query with crypto/security synonyms for better recall
        try:
            from rugbrain.core.retrieval import QueryExpander

            expanded_query = QueryExpander().expand(req.query)
        except Exception:
            expanded_query = req.query  # Fall through on expansion failure

        try:
            raw = await engine_three_pillar_search(
                query=expanded_query,
                collection=req.collection,
                top_k=req.top_k,
                min_similarity=req.min_similarity,
                filters=req.filters or None,
            )
            _record_rag_proxy("search", "success")
        except Exception as e:
            log.warning("rag_search_failed", error=str(e))
            _record_rag_proxy("search", "failure")
            raw = []
        took_ms = int((time.monotonic() - start) * 1000)
        hits = [self._wrap_hit(h, req.collection) for h in (raw or [])]
        return SearchResponse(
            query=req.query,
            hits=hits,
            total=len(hits),
            took_ms=took_ms,
            collection=req.collection,
        )

    async def ingest(self, req: IngestRequest) -> IngestResult:
        """Ingest a document into the RAG system."""
        log.info(
            "rag_ingest_started",
            collection=req.collection,
            content_len=len(req.content),
        )
        try:
            doc_id = req.doc_id or f"doc:{int(time.time() * 1000)}"
            r = await engine_ingest_document(
                collection=req.collection,
                doc_id=doc_id,
                content=req.content,
                metadata=req.metadata,
                content_type=req.metadata.get("content_type", "general") if req.metadata else "general",
            )
            return IngestResult(
                doc_id=doc_id,
                collection=req.collection,
                status=r.get("status", "ok"),
                chunks=r.get("chunks", 0),
            )
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("rag_ingest_failed", error=str(e))
            return IngestResult(
                doc_id=req.doc_id or "",
                collection=req.collection,
                status="failed",
                error=str(e),
            )

    async def stats(self) -> dict[str, Any]:
        """Return engine stats (collection counts + active embedder backend)."""
        try:
            return engine_get_stats()
        except Exception as e:
            log.warning("rag_stats_failed", error=str(e))
            return {"total_docs": 0, "backend": "unknown", "collections": [], "error": str(e)}

    async def record_feedback(self, record: FeedbackRecord) -> bool:
        """Record a scanner → RAG feedback (ingest a known scam)."""
        log.info(
            "rag_feedback_recorded",
            address=record.token_address[:12],
            action=record.action,
            score=record.safety_score,
        )
        try:
            content = (
                f"Token: {record.token_address}\n"
                f"Chain: {record.chain}\n"
                f"Safety score: {record.safety_score}\n"
                f"Risk flags: {', '.join(record.risk_flags)}"
            )
            metadata = {
                "source": record.source,
                "chain": record.chain,
                "safety_score": record.safety_score,
                "action": record.action,
            }
            req = IngestRequest(
                collection="known_scams",
                content=content,
                doc_id=record.token_address,
                metadata=metadata,
            )
            result = await self.ingest(req)
            return result.status == "ok"
        except Exception as e:
            log.warning("rag_feedback_failed", error=str(e))
            return False

    @staticmethod
    def _wrap_hit(raw: Any, collection: str) -> SearchHit:
        """Normalize a raw hit (dict or object) to Pydantic SearchHit."""
        if isinstance(raw, dict):
            return SearchHit(
                content=str(raw.get("content") or raw.get("text") or ""),
                score=float(raw.get("score", raw.get("similarity", 0)) or 0),
                metadata=raw.get("metadata", {}) or {},
                collection=str(raw.get("collection") or collection),
                doc_id=str(raw.get("id") or raw.get("doc_id") or ""),
            )
        return SearchHit(
            content=str(getattr(raw, "content", "") or getattr(raw, "text", "") or ""),
            score=float(getattr(raw, "score", 0) or 0),
            metadata=getattr(raw, "metadata", {}) or {},
            collection=str(getattr(raw, "collection", "") or collection),
            doc_id=str(getattr(raw, "id", "") or ""),
        )


async def init_rag() -> None:
    """Initialize the RAG system. Called from the host application's startup hook.

    v3: no-op (the new engine is lazy - collections load on first search).
    Kept for backward compat with lifespan hooks.
    """
    log.info("rag_init_started", note="v3 engine is lazy - no warmup needed")
    try:
        # Touch the cache backend to fail fast if misconfigured.
        from rugbrain.storage.cache import get_cache

        get_cache().ping()
        log.info("rag_init_complete")
    except Exception as e:
        log.warning("rag_init_redis_probe_failed", error=str(e))
