"""
RAG Search Engine — scam/exploit and coding knowledge search.

Thin wrappers over ``search_multi_collection`` that pin the knowledge
collections and enrich coding results with code blocks / code-first answers.
"""

import logging
from typing import Any

from .code_chunks import _CODE_MARKER_RE, extract_code_blocks, format_code_answer
from .constants import CODING_COLLECTIONS, SCAM_COLLECTIONS
from .query_intent import resolve_search_collections

logger = logging.getLogger(__name__)


async def search_scam_knowledge(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.4,
) -> dict[str, Any]:
    """Search the scam/exploit knowledge base.

    Defaults to SCAM_COLLECTIONS (defi_exploits, scam_patterns,
    crypto_vulnerabilities). Returns results plus the routed collection list
    so callers can see which knowledge buckets were consulted.
    """
    colls = resolve_search_collections(collections or list(SCAM_COLLECTIONS), query)
    from rugbrain.search import engine as _e

    results = await _e.search_multi_collection(
        query, collections=colls, limit=limit, min_similarity=min_similarity
    )
    return {
        "query": query,
        "collections": colls,
        "scam_knowledge": True,
        "results": results,
        "total": len(results),
    }


async def search_coding_knowledge(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.4,
) -> dict[str, Any]:
    """Search the smart-contract coding-knowledge base.

    Defaults to CODING_COLLECTIONS (coding_knowledge, solidity_patterns,
    solana_patterns, contract_security, audit_findings). Results whose content
    carries code (contract/function/struct bodies) are flagged with
    ``has_code_snippet`` and enriched with ``code_blocks`` (fenced blocks
    extracted intact, language-tagged) and ``code_answer`` (the content with
    fenced code surfaced verbatim) so callers can render code-first answers
    without flattening code into prose. Returns results plus the routed
    collection list so callers can see which knowledge buckets were consulted.
    """
    colls = resolve_search_collections(collections or list(CODING_COLLECTIONS), query)
    from rugbrain.search import engine as _e

    results = await _e.search_multi_collection(
        query, collections=colls, limit=limit, min_similarity=min_similarity
    )
    for r in results:
        content = r.get("content", "") or ""
        r["has_code_snippet"] = bool(_CODE_MARKER_RE.search(content.lower()))
        r["code_blocks"] = extract_code_blocks(content)
        r["code_answer"] = format_code_answer(content)

    # Crypto Code Mode: merge ContextCrypto collection hits (codebase_reference
    # + security_patterns) for crypto-CODE queries. Degrades silently.
    try:
        from rugbrain.crypto_code_retrieval import augment_coding_results

        results = await augment_coding_results(query, results, limit=limit)
    except Exception as exc:  # best-effort enrichment, never breaks coding search
        logger.debug("crypto_code_augment_failed err=%s", exc)
    return {
        "query": query,
        "collections": colls,
        "coding_knowledge": True,
        "results": results,
        "total": len(results),
    }
