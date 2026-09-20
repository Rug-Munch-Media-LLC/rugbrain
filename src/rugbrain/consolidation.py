"""Unified-corpus schema helpers (rag_documents).

One home for the content-addressed identity used by the consolidation:
``doc_id = {source}:{content_type}:{sha256(text)[:16]}`` and its uuid5 point
id, plus the legacy-source → content_type map. Kept separate so both the
consolidation script and future writers (news ingest) share one definition.
"""

from __future__ import annotations

import hashlib
import uuid

UNIFIED_COLLECTION = "rag_documents"
UUID_NS = uuid.NAMESPACE_URL
TEXT_FIELDS = ("content", "text", "chunk_text", "body", "title", "summary", "description")

CONTENT_TYPE_BY_SOURCE = {
    "news_v2": "news",
    "news_articles": "news",
    "social_intel": "social",
    "rag_documents": "general",
    "scam_intel_v2": "scam",
    "known_scams": "scam",
    "scam_patterns": "scam",
    "wallet_profiles": "wallet",
    "wallet_v2": "wallet",
    "defi_exploits": "exploit",
    "threat_intel_v2": "threat",
    "contract_audits": "audit",
    "github_audits": "audit",
    "crypto_vulnerabilities": "vulnerability",
    "regulation_enforcement": "regulation",
    "research_v2": "research",
    "education": "education",
}


def text_of(payload: dict) -> str:
    """Best text field for embedding, top-level or nested in ``metadata``."""
    md = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    for source in (payload, md):
        for field in TEXT_FIELDS:
            value = source.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def doc_id(source: str, content_type: str, text: str) -> str:
    """Content-addressed id — identical text upserts in place."""
    return f"{source}:{content_type}:{hashlib.sha256(text.encode()).hexdigest()[:16]}"


def point_id(doc_id_value: str) -> str:
    """Deterministic Qdrant point id for a doc_id."""
    return str(uuid.uuid5(UUID_NS, doc_id_value))


__all__ = ["CONTENT_TYPE_BY_SOURCE", "UNIFIED_COLLECTION", "doc_id", "point_id", "text_of"]
