"""Continuous RAG ingestion pipeline — digest scanned material into vector storage.

Data sources (what gets ingested):
  - Scanner results (token security reports)
  - Wallet labels and entities
  - News articles and market intelligence
  - Research documents and analysis
  - Agent conversation summaries
  - DeFi protocol documentation
  - Scam pattern documentation

Pipeline stages (in order):
  1. ``DocumentProcessor``  — chunk text into configurable segments with overlap
  2. ``QualityFilter``      — reject low-quality / spam / duplicate content
  3. ``RateLimiter``        — prevent system overload (max N docs per minute)
  4. ``BatchIngestor``      — accumulate and flush in configurable batches
  5. ``IngestionScheduler`` — schedule periodic ingestion sweeps
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from rugbrain._logging import get_logger
from rugbrain.core.chunking import content_hash as _content_hash
from rugbrain.core.chunking import map_source_type_to_content_type as _content_type_for
from rugbrain.core.config import (
    RAG_CHUNK_SIZE,
    RAG_INGESTION_BATCH_SIZE,
    RAG_SCHEDULER_INTERVAL,
)
from rugbrain.core.quality import QualityFilter as QualityFilter
from rugbrain.ingest.ingestion_models import (
    Document as Document,
)
from rugbrain.ingest.ingestion_models import (
    IngestionStatus as IngestionStatus,
)
from rugbrain.ingest.ingestion_models import (
    IngestReport as IngestReport,
)
from rugbrain.ingest.processor import DocumentProcessor as DocumentProcessor
from rugbrain.ingest.rate_limiter import RateLimiter as RateLimiter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

import contextlib

log = get_logger(__name__)


class BatchIngestor:
    """Accumulate documents and flush in batches of N.

    The core ingestion pipeline:
      1. Accept documents via ``ingest()`` or ``ingest_batch()``.
      2. Process through DocumentProcessor -> QualityFilter -> RateLimiter.
      3. Delegate to ``app.rag.engine.ingest_document`` for embedding + storage.
      4. Return an ``IngestReport`` with per-document status.
    """

    def __init__(
        self,
        batch_size: int = RAG_INGESTION_BATCH_SIZE,
        processor: DocumentProcessor | None = None,
        quality_filter: QualityFilter | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.batch_size = batch_size
        self._processor = processor or DocumentProcessor()
        self._filter = quality_filter or QualityFilter()
        self._rate_limiter = rate_limiter or RateLimiter()
        self._pending: list[Document] = []

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def ingest(
        self,
        content: str,
        collection: str = "scanner",
        doc_id: str = "",
        metadata: dict[str, Any] | None = None,
        source_type: str = "",
    ) -> IngestReport:
        doc = Document(
            content=content,
            collection=collection,
            doc_id=doc_id or f"ingest:{int(time.time() * 1000)}:{hash(content) % 10**8}",
            metadata=metadata or {},
            source_type=source_type,
        )
        return await self.ingest_batch([doc])

    async def ingest_batch(self, docs: list[Document]) -> IngestReport:
        start = time.monotonic()
        report = IngestReport(total=len(docs))
        if not docs:
            return report

        accepted, rejected = self._filter.filter_batch(docs)
        report.rejected = len(rejected)
        for doc, reason in rejected:
            log.info("doc_rejected", doc_id=doc.doc_id, reason=reason)

        if not accepted:
            report.status = IngestionStatus.REJECTED
            report.took_ms = int((time.monotonic() - start) * 1000)
            return report

        estimated_ops = sum(max(1, len(d.content) // RAG_CHUNK_SIZE) for d in accepted)
        if not await self._rate_limiter.check(estimated_ops):
            report.rate_limited = len(accepted)
            report.status = IngestionStatus.RATE_LIMITED
            report.took_ms = int((time.monotonic() - start) * 1000)
            return report

        from rugbrain.core.engine import ingest_document as engine_ingest

        errors: list[str] = []
        accepted_count = 0
        total_chunks = 0

        for doc in accepted:
            try:
                result = await engine_ingest(
                    collection=doc.collection,
                    doc_id=doc.doc_id,
                    content=doc.content,
                    metadata=doc.metadata,
                    chunk=True,
                    content_type=_content_type_for(doc.source_type),
                )
                if result["status"] == "ok":
                    accepted_count += 1
                    total_chunks += result.get("chunks", 0)
                elif result["status"] == "duplicate":
                    report.duplicate += 1
                else:
                    errors.append(f"{doc.doc_id}:{result['status']}")
            except Exception as e:
                log.warning("ingest_doc_failed", doc_id=doc.doc_id, error=str(e))
                errors.append(f"{doc.doc_id}:{e!s}")
                report.failed += 1

        report.accepted = accepted_count
        report.chunks_total = total_chunks
        report.errors = errors[:20]
        report.status = (
            IngestionStatus.COMPLETED
            if accepted_count == len(accepted)
            else IngestionStatus.PARTIAL
            if accepted_count > 0
            else IngestionStatus.FAILED
        )
        report.took_ms = int((time.monotonic() - start) * 1000)
        return report

    async def queue(self, doc: Document) -> int:
        self._pending.append(doc)
        if len(self._pending) >= self.batch_size:
            await self.flush()
        return len(self._pending)

    async def queue_many(self, docs: list[Document]) -> int:
        self._pending.extend(docs)
        if len(self._pending) >= self.batch_size:
            await self.flush()
        return len(self._pending)

    async def flush(self) -> IngestReport | None:
        if not self._pending:
            return None
        batch = self._pending[: self.batch_size]
        self._pending = self._pending[self.batch_size :]
        return await self.ingest_batch(batch)

    async def flush_all(self) -> list[IngestReport]:
        reports: list[IngestReport] = []
        while self._pending:
            report = await self.flush()
            if report:
                reports.append(report)
        return reports


class IngestionScheduler:
    """Schedule periodic ingestion jobs."""

    def __init__(self, ingestor: BatchIngestor | None = None, interval: int = RAG_SCHEDULER_INTERVAL) -> None:
        self._ingestor = ingestor or BatchIngestor()
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._handlers: list[Callable[[], Awaitable[None]]] = []

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def interval(self) -> int:
        return self._interval

    def register_handler(self, handler: Callable[[], Awaitable[None]]) -> None:
        self._handlers.append(handler)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def tick(self) -> list[IngestReport]:
        reports: list[IngestReport] = []
        batch_report = await self._ingestor.flush_all()
        if batch_report:
            reports.extend(batch_report)
        for handler in self._handlers:
            try:
                await handler()
            except Exception as e:
                log.warning("scheduler_handler_failed", handler=handler.__name__, error=str(e))
        return reports

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.tick()
            except Exception as e:
                log.error("scheduler_tick_failed", error=str(e))
            await asyncio.sleep(self._interval)


# ── Source-specific ingestion helpers ────────────────────────────────


async def ingest_scan_result(
    token_address: str,
    chain: str,
    safety_score: float,
    risk_flags: list[str],
    symbol: str = "",
    name: str = "",
    extra: dict[str, Any] | None = None,
    ingestor: BatchIngestor | None = None,
) -> IngestReport:
    metadata = {
        "token_address": token_address,
        "chain": chain,
        "safety_score": safety_score,
        "risk_flags": risk_flags,
        "symbol": symbol,
        "name": name,
        "source": "scanner",
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if extra:
        metadata.update(extra)
    content = f"Token: {token_address}\nChain: {chain}\nSymbol: {symbol or '—'}\nName: {name or '—'}\nSafety score: {safety_score}/100\nRisk flags: {', '.join(risk_flags)}\n"
    _ingestor = ingestor or _default_ingestor
    return await _ingestor.ingest(
        content=content,
        collection="scanner",
        doc_id=f"scan:{chain}:{token_address}",
        metadata=metadata,
        source_type="scanner",
    )


async def ingest_wallet_profile(
    wallet_address: str,
    chain: str,
    labels: list[str],
    risk_score: float = 0.0,
    entities: list[str] | None = None,
    extra: dict[str, Any] | None = None,
    ingestor: BatchIngestor | None = None,
) -> IngestReport:
    metadata = {
        "wallet_address": wallet_address,
        "chain": chain,
        "labels": labels,
        "risk_score": risk_score,
        "entities": entities or [],
        "source": "wallet_intel",
    }
    if extra:
        metadata.update(extra)
    content = f"Wallet: {wallet_address}\nChain: {chain}\nLabels: {', '.join(labels)}\nRisk score: {risk_score}\nEntities: {', '.join(entities or [])}\n"
    _ingestor = ingestor or _default_ingestor
    return await _ingestor.ingest(
        content=content,
        collection="wallet",
        doc_id=f"wallet:{chain}:{wallet_address}",
        metadata=metadata,
        source_type="wallet",
    )


async def ingest_news_article(
    title: str,
    body: str,
    source_url: str,
    source_name: str = "",
    published_at: str = "",
    categories: list[str] | None = None,
    sentiment: float = 0.0,
    ingestor: BatchIngestor | None = None,
) -> IngestReport:
    metadata = {
        "title": title,
        "source_url": source_url,
        "source_name": source_name or "",
        "published_at": published_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "categories": categories or [],
        "sentiment": sentiment,
        "source": "news",
    }
    content = f"Title: {title}\n\n{body}" if title else body
    doc_id = f"news:{_content_hash(source_url + title)[:12]}"
    _ingestor = ingestor or _default_ingestor
    return await _ingestor.ingest(
        content=content, collection="news", doc_id=doc_id, metadata=metadata, source_type="news"
    )


async def ingest_research_post(
    post_id: str,
    title: str,
    content: str,
    category: str = "",
    tags: list[str] | None = None,
    author: str = "",
    ingestor: BatchIngestor | None = None,
) -> IngestReport:
    metadata = {
        "post_id": post_id,
        "title": title,
        "category": category,
        "tags": tags or [],
        "author": author,
        "source": "bulletin_board",
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    full_content = f"Title: {title}\n\n{content}" if title else content
    _ingestor = ingestor or _default_ingestor
    return await _ingestor.ingest(
        content=full_content,
        collection="research",
        doc_id=f"research:{post_id}",
        metadata=metadata,
        source_type="research",
    )


async def ingest_scam_pattern(
    pattern_name: str,
    description: str,
    severity: str = "medium",
    indicators: list[str] | None = None,
    references: list[str] | None = None,
    ingestor: BatchIngestor | None = None,
) -> IngestReport:
    metadata = {
        "pattern_name": pattern_name,
        "severity": severity,
        "indicators": indicators or [],
        "references": references or [],
        "source": "scam_patterns",
    }
    indicators_text = "\n".join(f"  - {i}" for i in (indicators or []))
    refs_text = "\n".join(f"  - {r}" for r in (references or []))
    content = f"Pattern: {pattern_name}\nSeverity: {severity}\nDescription: {description}\nIndicators:\n{indicators_text}\nReferences:\n{refs_text}\n"
    _ingestor = ingestor or _default_ingestor
    return await _ingestor.ingest(
        content=content,
        collection="scams",
        doc_id=f"scam:{_content_hash(pattern_name)[:12]}",
        metadata=metadata,
        source_type="scam",
    )


# ── Convenience singleton ────────────────────────────────────────────

_default_processor = DocumentProcessor()
_default_filter = QualityFilter()
_default_limiter = RateLimiter()
_default_ingestor = BatchIngestor(
    processor=_default_processor, quality_filter=_default_filter, rate_limiter=_default_limiter
)
_default_scheduler = IngestionScheduler(ingestor=_default_ingestor)


def get_processor() -> DocumentProcessor:
    return _default_processor


def get_filter() -> QualityFilter:
    return _default_filter


def get_rate_limiter() -> RateLimiter:
    return _default_limiter


def get_ingestor() -> BatchIngestor:
    return _default_ingestor


def get_scheduler() -> IngestionScheduler:
    return _default_scheduler
