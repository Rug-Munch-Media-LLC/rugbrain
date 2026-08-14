"""Ingestion pipeline — chunking, dedup, quality gates, rate limiting, crawler."""

from __future__ import annotations

from rugbrain.ingest._crawler_constants import DEFAULT_COLLECTION
from rugbrain.ingest.ingestion import BatchIngestor, IngestionScheduler, get_ingestor, get_scheduler
from rugbrain.ingest.ingestion_models import Chunk, Document, IngestionStatus, IngestReport
from rugbrain.ingest.processor import DocumentProcessor
from rugbrain.ingest.rate_limiter import RateLimiter

__all__ = [
    "BatchIngestor",
    "Chunk",
    "DEFAULT_COLLECTION",
    "Document",
    "DocumentProcessor",
    "IngestReport",
    "IngestionScheduler",
    "IngestionStatus",
    "RateLimiter",
    "get_ingestor",
    "get_scheduler",
]
