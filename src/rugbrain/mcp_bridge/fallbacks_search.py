"""RAG fallbacks for the search/trace bridge tools.

Used when the MCP is unreachable: RugBrain answers from its own coding
knowledge. Guarded: queries are sanitized before the RAG search and result
content is redacted for secret patterns before returning.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _fallback_search(query: str, *, limit: int) -> dict[str, Any]:
    """Use RugBrain's own coding knowledge when the MCP is unavailable.

    Guarded: the query is sanitized before the RAG search and result
    content is redacted for secret patterns before returning.
    """
    from rugbrain import mcp_bridge as _pkg

    safe_query, guard = _pkg._guard_query(query)
    if guard["refused"]:
        return {
            "ok": False,
            "bridge": "guarded",
            "tool": "search_codebase",
            "query": query,
            "results": [],
            "total": 0,
            "raw": None,
            "guard": guard,
            "error": "query_refused_after_injection_sanitization",
        }
    try:
        from rugbrain.search_service import search_coding_knowledge  # lazy import

        res = await search_coding_knowledge(safe_query, limit=limit)
        results = []
        for r in res.get("results", []) or []:
            results.append(
                _pkg._redact_result(
                    {
                        "kind": "code" if r.get("has_code_snippet") else "knowledge",
                        "id": r.get("id"),
                        "content": r.get("content", ""),
                        "code_blocks": r.get("code_blocks", []),
                        "code_answer": r.get("code_answer", ""),
                        "score": r.get("similarity", 0.0),
                        "metadata": r.get("metadata", {}),
                        "source": r.get("source", ""),
                    },
                    ("content", "code_answer", "source"),
                )
            )
        return {
            "ok": True,
            "bridge": "rag_fallback",
            "tool": "search_codebase",
            "query": query,
            "results": results,
            "total": len(results),
            "raw": None,
            "guard": guard,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("rag_fallback_search failed err=%s", str(exc)[:160])
        return {
            "ok": False,
            "bridge": "rag_fallback",
            "tool": "search_codebase",
            "query": query,
            "results": [],
            "total": 0,
            "raw": None,
            "guard": guard,
            "error": str(exc)[:200],
        }


async def _fallback_trace(function: str, *, depth: int) -> dict[str, Any]:
    """Return coding knowledge about *function* when the MCP is unavailable.

    Guarded: the function symbol is sanitized and result content redacted
    via the search fallback (defense in depth for the answer path).
    """
    from rugbrain import mcp_bridge as _pkg

    fallback = await _pkg._fallback_search(function, limit=depth * 3)
    return {
        "ok": fallback.get("ok", False),
        "bridge": "rag_fallback",
        "tool": "trace_codebase",
        "function": function,
        "trace": None,
        "security_flags": [],
        "backend": "rag_fallback",
        "results": fallback.get("results", []),
        "guard": fallback.get("guard", {}),
    }
