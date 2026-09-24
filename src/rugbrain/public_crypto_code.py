"""Public crypto-coding retrieval from the reviewed RugBrain projection.

This module serves public/open-source code knowledge, not ContextCrypto's
internal collections. It refuses requests to operationalize exploits against
live systems or build clearly harmful tooling, while allowing defensive
analysis, public advisories, and authorized code review.
"""

from __future__ import annotations

import re
from typing import Any

from rugbrain.knowledge_boundary import sanitize_public_result
from rugbrain.public_projection import (
    PUBLIC_CODE_SOURCE_KIND,
    PUBLIC_PROJECTION_COLLECTION,
    PUBLIC_SOURCE_KIND,
)

_DANGEROUS_REQUEST = re.compile(
    r"(?ix)"
    r"(?:\b(?:deploy|execute|run|launch|automate)\b[^.]{0,80}\b(?:exploit|malware|ransomware|dropper|shell)\b"
    r"|\b(?:drain|steal|exfiltrate|brute[- ]?force|bypass|evade|disable)\b[^.]{0,80}"
    r"\b(?:wallet|credential|seed phrase|private key|authentication|detection|mainnet|live target)\b"
    r"|\b(?:write|generate|build|create)\b[^.]{0,50}\b(?:malware|ransomware|keylogger|botnet)\b"
    r"|\bwithout\s+authorization\b|\bunauthorized\s+(?:access|attack|exploit)\b"
    r"|\bexploit\b[^.]{0,50}\b(?:mainnet|production|live)\b)"
)

_PUBLIC_SOURCE_KINDS = frozenset({PUBLIC_SOURCE_KIND, PUBLIC_CODE_SOURCE_KIND})


def public_code_request_allowed(query: str) -> bool:
    """Return False for requests that would enable operational harm."""
    return not _DANGEROUS_REQUEST.search((query or "").strip())


def _is_public_projection_hit(hit: dict[str, Any]) -> bool:
    metadata = hit.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return (
        metadata.get("audience") == "public" and metadata.get("source_kind") in _PUBLIC_SOURCE_KINDS
    )


async def search_public_crypto_code(query: str, top_k: int = 10) -> dict[str, Any]:
    """Search reviewed public crypto code/pattern projections.

    The route must call this function instead of the internal ContextCrypto
    retriever. Defense-oriented questions and public exploit explanations are
    allowed; requests to weaponize or execute an attack are refused.
    """
    if not public_code_request_allowed(query):
        return {
            "query": query,
            "routed": False,
            "blocked": True,
            "reason": "unsafe_request",
            "results": [],
            "total": 0,
            "message": "I can help with defensive analysis, public advisories, and authorized code review.",
        }

    from rugbrain.embeddings import get_query_embedding
    from rugbrain.qdrant_store import QdrantVectorStore

    embedding = await get_query_embedding(query)
    store = QdrantVectorStore()
    try:
        raw = await store.hybrid_search(
            query_text=query,
            query_embedding=embedding,
            collection=PUBLIC_PROJECTION_COLLECTION,
            limit=top_k,
            min_similarity=0.35,
        )
    finally:
        await store.close()

    results = [sanitize_public_result(hit) for hit in raw if _is_public_projection_hit(hit)]
    return {
        "query": query,
        "routed": True,
        "blocked": False,
        "audience": "public",
        "source": PUBLIC_PROJECTION_COLLECTION,
        "results": results,
        "total": len(results),
    }


__all__ = ["public_code_request_allowed", "search_public_crypto_code"]
