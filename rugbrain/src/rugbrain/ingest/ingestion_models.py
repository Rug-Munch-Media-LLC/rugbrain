"""Models for the RAG ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class IngestionStatus(StrEnum):
    """Status of an ingestion operation."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"
    PARTIAL = "partial"


@dataclass
class Document:
    """A document ready for ingestion."""

    content: str
    collection: str = "scanner"
    doc_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    source_type: str = ""
    ingested_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class Chunk:
    """A single chunk produced by DocumentProcessor."""

    text: str
    content_hash: str
    index: int
    quality: float = 1.0


@dataclass
class IngestReport:
    """Summary report for a batch ingestion."""

    total: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicate: int = 0
    rate_limited: int = 0
    failed: int = 0
    chunks_total: int = 0
    took_ms: int = 0
    errors: list[str] = field(default_factory=list)
    status: IngestionStatus = IngestionStatus.PENDING
