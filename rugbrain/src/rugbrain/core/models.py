"""Pydantic v2 models for the RAG domain."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EmbeddingProvider(StrEnum):
    """Embedding provider options."""

    OPENAI = "openai"
    OPENROUTER = "openrouter"
    HUGGINGFACE = "huggingface"
    COHERE = "cohere"


# Default RAG collections — neutral, deployment-specific names. Point
# ``RAG_COLLECTIONS`` (or ``COLLECTIONS``) at your own namespaces; the
# package makes no assumptions about private collection contracts.
COLLECTIONS: list[str] = [
    "docs",
    "news",
    "research",
]


class SearchRequest(BaseModel):
    """RAG search request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(..., min_length=1, max_length=2048)
    collection: str = Field(default="docs")
    top_k: int = Field(default=5, ge=1, le=50)
    min_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    filters: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    """A single search result."""

    content: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    collection: str = ""
    doc_id: str = ""


class SearchResponse(BaseModel):
    """RAG search response."""

    query: str
    hits: list[SearchHit] = Field(default_factory=list)
    total: int = 0
    took_ms: int = 0
    collection: str = ""


class IngestRequest(BaseModel):
    """RAG document ingestion request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    collection: str = Field(default="docs")
    content: str = Field(..., min_length=1)
    doc_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    """RAG document ingestion result."""

    doc_id: str
    collection: str
    status: str = "ok"  # ok | failed
    chunks: int = 0
    error: str | None = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)


class FeedbackRecord(BaseModel):
    """Scanner → RAG feedback record."""

    model_config = ConfigDict(str_strip_whitespace=True)

    token_address: str
    chain: str = "solana"
    safety_score: float
    risk_flags: list[str] = Field(default_factory=list)
    action: str = "ingest"  # ingest | remove | update
    source: str = "scanner"
