"""RAG Query Rewriter — multi-query expansion (#5).

Generates 2-3 query variants from the user's original query,
retrieves results for each, and fuses via reciprocal rank fusion.

Architecture:
  1. Classify query type (entity/address, keyword, natural language)
  2. Generate variants using LLM via ai_router.chat_completion
  3. Retrieve results for original + variants
  4. RRF fuse all result sets
"""

# NOTE (deprecated): archived in _legacy/ — kept for reference only, has zero
# live references, and is not type-checked in CI. Per-line pyright ignores
# below silence diagnostics that would otherwise require porting private
# monorepo dependencies that do not exist in the OSS rugbrain package.

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("rag.query_rewriter")

# ── Query classification ────────────────────────────────────

ADDRESS_PATTERN = re.compile(r"0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44}")
ENTITIY_PATTERN = re.compile(
    r"^(?:what|who|tell me about|lookup|search|find)\s+(?:is\s+)?([A-Z][a-zA-Z]{2,30})$"
)


def classify_query(query: str) -> str:
    """Classify a query: entity_heavy, keyword_heavy, semantic_heavy, balanced."""
    lower = query.lower().strip()
    if ADDRESS_PATTERN.search(query):
        return "entity_heavy"
    if ENTITIY_PATTERN.match(lower):
        return "entity_heavy"
    words = lower.split()
    if len(words) <= 3:
        return "keyword_heavy"
    if any(w in lower for w in ["explain", "how", "why", "what is", "describe"]):
        return "semantic_heavy"
    return "balanced"


# ── Rewrite prompt template ──────────────────────────────────

REWRITE_PROMPT = """You are a search query rewriter for a crypto intelligence RAG system.
Given a user query, generate {n} alternative phrasings that would help find
relevant documents in a vector database of scam reports, wallet labels,
token analyses, and DeFi documentation.

Original: {query}

Rules:
- Keep each variant under 100 characters
- Use different phrasings and keywords
- For address/symbol queries, include chain names and scam terms
- Return as a JSON array of strings

Output only: ["variant1", "variant2", "variant3"]"""


async def rewrite_query(query: str, n_variants: int = 3, use_llm: bool = False) -> list[str]:
    """Generate query variants. Falls back to rule-based if no LLM available."""
    if not use_llm:
        return _rule_based_variants(query, n_variants)

    try:
        from app import ai_router  # pyright: ignore[reportAttributeAccessIssue]

        prompt = REWRITE_PROMPT.format(query=query, n=n_variants)
        result = await ai_router.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model="deepseek-v4-flash",
            temperature=0.3,
            max_tokens=200,
        )
        content = result.get("content", "[]")
        variants = json.loads(content)
        return variants[:n_variants]
    except Exception:
        logger.debug("Query rewrite via LLM failed, using rule-based fallback")
        return _rule_based_variants(query, n_variants)


def _rule_based_variants(query: str, n: int) -> list[str]:
    """Simple rule-based query expansion. Tokenizes and generates variants."""
    words = query.lower().split()
    variants = [query]
    scam_terms = ["scam", "honeypot", "rug pull", "exploit", "phishing", "fraud"]

    # For short queries, add scam-related terms
    if len(words) <= 4:
        for term in scam_terms:
            if term not in query.lower():
                variants.append(f"{query} {term}")
                if len(variants) >= n + 1:
                    break

    # For address queries, add label lookup variants
    if ADDRESS_PATTERN.search(query):
        variants.append(f"wallet labels for {query}")
        variants.append(f"transaction history {query}")

    # De-duplicate
    seen = set()
    result = []
    for v in variants:
        if v.lower() not in seen:
            seen.add(v.lower())
            result.append(v)
    return result[: n + 1]  # +1 for original


__all__ = ["classify_query", "rewrite_query"]
