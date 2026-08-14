"""DocumentProcessor — chunk raw text into overlapping segments for embedding."""

from __future__ import annotations

from rugbrain._logging import get_logger
from rugbrain.core.chunking import chunk_text as _chunk_text
from rugbrain.core.chunking import map_source_type_to_content_type as _content_type_for
from rugbrain.core.config import (
    RAG_CHUNK_OVERLAP,
    RAG_CHUNK_SIZE,
    RAG_MIN_CHUNK_CHARS,
    RAG_MIN_DOC_LENGTH,
)
from rugbrain.ingest.ingestion_models import Chunk, Document

log = get_logger(__name__)


class DocumentProcessor:
    """Chunk raw text into overlapping segments for embedding.

    Thin wrapper over ``app.rag.chunking.chunk_text`` (the single source of
    truth). Chunking is content-type aware: the strategy is selected per
    document from ``Document.source_type`` (e.g. news → paragraph-level,
    scanner → structured field-preserving, scam → finding-by-finding).
    """

    def __init__(
        self,
        max_chunk: int = RAG_CHUNK_SIZE,
        overlap: int = RAG_CHUNK_OVERLAP,
        min_chars: int = RAG_MIN_CHUNK_CHARS,
    ) -> None:
        self.max_chunk = max_chunk
        self.overlap = overlap
        self.min_chars = min_chars

    def chunk_text(self, text: str, content_type: str = "general") -> list[Chunk]:
        """Split text into overlapping, content-type-aware chunks.

        Args:
            text: Raw document text.
            content_type: Chunking strategy (solidity, whitepaper, audit,
                news, social, transaction, general). Defaults to "general".

        Returns:
            List of Chunk objects with quality scores.
        """
        raw = _chunk_text(
            text,
            content_type=content_type,
            chunk_size=self.max_chunk,
            chunk_overlap=self.overlap,
            min_chars=self.min_chars,
        )
        return [Chunk(text=c.text, content_hash=c.content_hash, index=c.index, quality=c.quality) for c in raw]

    def process_document(self, doc: Document) -> list[Chunk]:
        """Convenience: chunk a Document into chunks (content-type aware)."""
        if not doc.content or len(doc.content.strip()) < RAG_MIN_DOC_LENGTH:
            log.debug("doc_too_short", doc_id=doc.doc_id, length=len(doc.content))
            return []
        return self.chunk_text(doc.content, content_type=_content_type_for(doc.source_type))
