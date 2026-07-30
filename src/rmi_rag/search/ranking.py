"""
RAG Search — query classification and ranking logic.

Extracted from search_service.py to keep ranking/scoring concerns separate.
"""

import re
from typing import Any


def _classify_query(query: str) -> str:
    """Classify a query into entity_heavy, keyword_heavy, semantic_heavy, or balanced."""
    q = query.lower().strip()

    # Entity-heavy: contains hex addresses, $tickers, or known crypto symbols
    if re.search(r"0x[a-f0-9]{6,}", q):
        return "entity_heavy"
    if re.search(r"\$[a-z]{2,10}", q):
        return "entity_heavy"

    # Keyword-heavy: short, term-based, no natural language flow
    words = q.split()
    if len(words) <= 3 and not any(
        w in q
        for w in [
            "what",
            "how",
            "why",
            "when",
            "explain",
            "describe",
            "compare",
            "analyze",
        ]
    ):
        return "keyword_heavy"

    # Semantic-heavy: natural language questions
    if any(
        w in q
        for w in [
            "what is",
            "how does",
            "why did",
            "explain",
            "describe",
            "compare",
            "analyze",
            "tell me",
            "show me",
            "find",
        ]
    ):
        return "semantic_heavy"

    return "balanced"
