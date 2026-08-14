"""Quality filtering for the RAG ingestion pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rugbrain._logging import get_logger
from rugbrain.core.chunking import content_hash as _content_hash
from rugbrain.core.config import RAG_MIN_DOC_LENGTH, RAG_SPAM_REPETITION_THRESHOLD

if TYPE_CHECKING:
    from rugbrain.ingest.ingestion_models import Document

log = get_logger(__name__)


def quality_score(text: str, max_chunk: int) -> float:
    """Quick quality score 0-1. Penalise noise (high special-char ratio)."""
    if not text:
        return 0.0
    n = len(text)
    special = sum(1 for c in text if not c.isalnum() and not c.isspace())
    length_score = min(1.0, n / max_chunk)
    special_score = max(0.0, 1.0 - (special / n))
    return round(0.7 * length_score + 0.3 * special_score, 3)


class QualityFilter:
    """Reject low-quality content before it reaches the embedding pipeline.

    Checks:
      - Minimum document length (too-short documents are noise)
      - Spam / repetitive patterns (high n-gram repetition ratio)
      - Exact duplicate detection (via content_hash against Redis)
    """

    def __init__(
        self,
        min_length: int = RAG_MIN_DOC_LENGTH,
        spam_threshold: float = RAG_SPAM_REPETITION_THRESHOLD,
    ) -> None:
        self.min_length = min_length
        self.spam_threshold = spam_threshold

    def check(self, doc: Document) -> tuple[bool, str]:
        """Return (is_accepted, reason) for a document."""
        content = doc.content.strip()
        if len(content) < self.min_length:
            return False, f"too_short:{len(content)}<{self.min_length}"
        if self._is_spam(content):
            return False, "spam_repetitive"
        h = _content_hash(content)
        if self._is_duplicate(h, doc.collection):
            return False, "duplicate"
        return True, ""

    async def check_async(self, doc: Document) -> tuple[bool, str]:
        return self.check(doc)

    def filter_batch(self, docs: list[Document]) -> tuple[list[Document], list[tuple[Document, str]]]:
        """Filter a batch of documents. Returns (accepted, rejected_with_reason)."""
        accepted: list[Document] = []
        rejected: list[tuple[Document, str]] = []
        for doc in docs:
            ok, reason = self.check(doc)
            if ok:
                accepted.append(doc)
            else:
                rejected.append((doc, reason))
        return accepted, rejected

    @staticmethod
    def _is_spam(text: str, threshold: float = RAG_SPAM_REPETITION_THRESHOLD) -> bool:
        """Detect spam by checking trigram repetition ratio."""
        if len(text) < 100:
            return False
        words = text.lower().split()
        if len(words) < 10:
            return False
        trigrams = [" ".join(words[i : i + 3]) for i in range(len(words) - 2)]
        if not trigrams:
            return False
        unique = len(set(trigrams))
        ratio = 1.0 - (unique / len(trigrams))
        return ratio > threshold

    @staticmethod
    def _is_duplicate(content_hash: str, collection: str) -> bool:
        """Has this exact content already been ingested?"""
        try:
            from rugbrain.storage.cache import get_cache

            r = get_cache()
            if r is None:
                return False
            return bool(r.sismember(f"rag:hashes:{collection}", content_hash))
        except Exception as e:
            log.warning("dedup_check_failed: %s", e)
            return False
