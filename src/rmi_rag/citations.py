"""
SENTINEL - RAG Citation Integration
=====================================
Shared utility for all scanner modules to query RAG for relevant research,
known scams, and forensic reports, then include structured citations in their output.

Usage in scanners:
    from rmi_rag.citations import query_rag_citations

    citations = await query_rag_citations(
        topic="flash loan sandwich attack",
        chain="ethereum",
        address="0xabc...",
    )
    # citations is a list of CitationEntry dicts

Citations add credibility:
    "Token exhibits pattern matching flash loan sandwich attacks
     as described in Flashot (Zhang et al. 2021)"
    is way more credible than "suspicious activity detected."
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger("rag_citations")


# ── Citation relevance map: scanner topic → RAG collections to query ──

SCANNER_COLLECTIONS = {
    "flash_loan": {
        "primary": ["forensic_reports", "known_scams"],
        "secondary": ["transaction_patterns", "scam_patterns"],
    },
    "pump_dump": {
        "primary": ["forensic_reports", "known_scams", "market_intel"],
        "secondary": ["scam_patterns", "wallet_profiles"],
    },
    "oracle_manipulation": {
        "primary": ["forensic_reports", "scam_patterns"],
        "secondary": ["contract_audits", "known_scams"],
    },
    "governance_attack": {
        "primary": ["forensic_reports", "known_scams"],
        "secondary": ["contract_audits", "scam_patterns"],
    },
    "proxy_detect": {
        "primary": ["forensic_reports", "contract_audits"],
        "secondary": ["known_scams", "scam_patterns"],
    },
    "static_analysis": {
        "primary": ["contract_audits", "forensic_reports"],
        "secondary": ["scam_patterns", "known_scams"],
    },
    "decompiler": {
        "primary": ["contract_audits", "scam_patterns"],
        "secondary": ["forensic_reports", "known_scams"],
    },
    "address_labeling": {
        "primary": ["known_scams", "wallet_profiles"],
        "secondary": ["forensic_reports"],
    },
    # Generic fallback
    "general": {
        "primary": ["known_scams", "forensic_reports"],
        "secondary": ["scam_patterns", "wallet_profiles"],
    },
}


async def query_rag_citations(
    topic: str,
    chain: str = "ethereum",
    address: str | None = None,
    scanner_type: str = "general",
    max_citations: int = 5,
    min_similarity: float = 0.5,
) -> list[dict[str, Any]]:
    """Query RAG for citations relevant to a scanner's findings.

    Args:
        topic: What to search for (e.g., "flash loan sandwich attack",
               "pump and dump volume spike", "proxy upgrade rug pull")
        chain: Blockchain for context filtering
        address: Token address (included in query for specificity)
        scanner_type: Which scanner is asking (maps to relevant collections)
        max_citations: Max citations to return
        min_similarity: Minimum similarity score

    Returns:
        List of citation dicts with keys:
            source, title, collection, similarity, snippet, reference
    """
    try:
        from rmi_rag.search_service import search_multi_collection
    except ImportError:
        logger.debug("RAG service not available, skipping citations")
        return []

    # Build enriched query
    query_parts = [topic]
    if chain and chain.lower() != "general":
        query_parts.append(chain)
    if address:
        query_parts.append(address[:10])  # prefix only, not full address
    enriched_query = " ".join(query_parts)

    # Get collection priority for this scanner type
    coll_map = SCANNER_COLLECTIONS.get(scanner_type, SCANNER_COLLECTIONS["general"])
    primary = coll_map.get("primary", ["known_scams", "forensic_reports"])
    secondary = coll_map.get("secondary", [])

    citations = []

    try:
        # Query primary collections first (higher min_similarity)
        # Timeout: 30s max on first call (model loading), then 10s
        primary_results = await asyncio.wait_for(
            search_multi_collection(
                query=enriched_query,
                collections=primary,
                limit=max_citations,
                min_similarity=min_similarity,
            ),
            timeout=30.0,
        )

        for r in primary_results:
            citation = _format_citation(r, "primary")
            if citation:
                citations.append(citation)

        # If we need more, dip into secondary collections
        if len(citations) < max_citations and secondary:
            remaining = max_citations - len(citations)
            secondary_results = await asyncio.wait_for(
                search_multi_collection(
                    query=enriched_query,
                    collections=secondary,
                    limit=remaining,
                    min_similarity=min_similarity - 0.1,  # slightly lower threshold
                ),
                timeout=8.0,
            )
            for r in secondary_results:
                citation = _format_citation(r, "secondary")
                if citation:
                    citations.append(citation)

    except (TimeoutError, Exception) as e:
        logger.warning("RAG citation query failed for '%s': %s", topic, e)
        return []

    # Sort by similarity, deduplicate
    citations.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    seen = set()
    unique = []
    for c in citations:
        key = (c.get("title", ""), c.get("collection", ""))
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique[:max_citations]


async def query_address_rag(
    address: str,
    chain: str = "ethereum",
    max_results: int = 5,
) -> list[dict[str, Any]]:
    """Quick RAG lookup for a specific address across scam/wallet collections.

    Used by address_labeler and any scanner that needs to check if an address
    is already known in our RAG databases.
    """
    try:
        from rmi_rag.search_service import search_multi_collection
    except ImportError:
        return []

    try:
        results = await asyncio.wait_for(
            search_multi_collection(
                query=address,
                collections=["known_scams", "wallet_profiles", "forensic_reports"],
                limit=max_results,
                min_similarity=0.4,
            ),
            timeout=10.0,
        )
        formatted = []
        for r in results:
            c = _format_citation(r, "address_lookup")
            if c:
                formatted.append(c)
        return formatted
    except Exception as e:
        logger.debug("Address RAG query failed for %s...: %s", address[:10], e)
        return []


def _format_citation(raw: dict[str, Any], tier: str) -> dict[str, Any] | None:
    """Turn a RAG search result into a clean citation dict."""
    if not raw:
        return None

    # Extract the best text field
    content = raw.get("content", "") or raw.get("text", "") or raw.get("summary", "")
    if not content:
        return None

    # Build a short snippet (first 200 chars)
    snippet = content[:200].strip()
    if len(content) > 200:
        snippet += "..."

    # Extract title if available
    title = raw.get("title", "") or raw.get("name", "") or raw.get("id", "")
    if not title:
        # Use first meaningful line
        for line in content.split("\n"):
            line = line.strip()
            if len(line) > 10:
                title = line[:80]
                break

    # Build reference string
    collection = raw.get("collection", "unknown")
    similarity = raw.get("similarity", 0.0)

    # Format a human-readable reference
    if collection == "forensic_reports":
        ref_prefix = "Research"
    elif collection == "known_scams":
        ref_prefix = "Known Scam DB"
    elif collection == "wallet_profiles":
        ref_prefix = "Wallet Intel"
    elif collection == "contract_audits":
        ref_prefix = "Audit"
    elif collection == "scam_patterns":
        ref_prefix = "Pattern DB"
    elif collection == "market_intel":
        ref_prefix = "Market Intel"
    else:
        ref_prefix = "RAG"

    reference = f"[{ref_prefix}]"
    if title:
        reference += f" {title}"

    return {
        "source": collection,
        "title": title,
        "collection": collection,
        "similarity": round(similarity, 3),
        "snippet": snippet,
        "reference": reference,
        "tier": tier,
    }


def build_citation_string(citations: list[dict[str, Any]], finding: str) -> str:
    """Build a natural-language citation string for a finding.

    Example output:
        "Flash loan sandwich pattern detected. Supported by: [Research] Flashot:
        Arbitrage Attack in DeFi (Zhang et al. 2021) [sim: 0.89]; [Known Scam DB]
        0xabc4... flash loan exploit [sim: 0.82]"
    """
    if not citations:
        return finding

    parts = [finding, " Supported by:"]
    for i, c in enumerate(citations[:3]):  # max 3 inline
        ref = c.get("reference", "RAG")
        sim = c.get("similarity", 0)
        parts.append(f" {ref} [sim: {sim:.2f}]")
        if i < len(citations) - 1 and i < 2:
            parts.append(";")

    return "".join(parts)

