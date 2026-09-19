"""RAG fallbacks for the security/reference bridge tools.

Used when the MCP is unreachable: results are shaped like the
corresponding MCP responses, sourced from RugBrain's own knowledge.
Guarded: content is redacted before returning.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def _fallback_security(query: str, *, top_k: int) -> dict[str, Any]:
    """Search RugBrain's scam/coding KB for security patterns.

    Guarded: the query is sanitized before the RAG search and result
    content is redacted for secret patterns before returning.
    """
    from rugbrain import mcp_bridge as _pkg

    safe_query, guard = _pkg._guard_query(query)
    if guard["refused"]:
        return {
            "ok": False,
            "bridge": "guarded",
            "tool": "get_security_context",
            "query": query,
            "results": [],
            "count": 0,
            "guard": guard,
            "error": "query_refused_after_injection_sanitization",
        }
    try:
        from rugbrain.search_service import search_scam_knowledge  # lazy import

        res = await search_scam_knowledge(safe_query, limit=top_k)
        results = []
        for r in res.get("results", []) or []:
            results.append(
                _pkg._redact_result(
                    {
                        "id": r.get("id"),
                        "title": r.get("source", "pattern"),
                        "description": r.get("content", "")[:400],
                        "relevance_score": r.get("similarity", 0.0),
                        "metadata": r.get("metadata", {}),
                    },
                    ("title", "description"),
                )
            )
        return {
            "ok": True,
            "bridge": "rag_fallback",
            "tool": "get_security_context",
            "query": query,
            "results": results[:top_k],
            "count": len(results),
            "guard": guard,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("rag_fallback_security failed err=%s", str(exc)[:160])
        return {
            "ok": False,
            "bridge": "rag_fallback",
            "tool": "get_security_context",
            "query": query,
            "results": [],
            "count": 0,
            "guard": guard,
            "error": str(exc)[:200],
        }


async def _fallback_resolve(query: str, *, guard: dict[str, Any]) -> dict[str, Any]:
    """Resolve a library name from RugBrain's own coding KB when MCP is down.

    Shapes results like the MCP ``resolve_library`` response (``matches`` with
    ``library_id``/``library_name``), sourced from coding-knowledge hits.
    Guarded: content redacted before returning.
    """
    from rugbrain import mcp_bridge as _pkg

    try:
        from rugbrain.search_service import search_coding_knowledge  # lazy import

        res = await search_coding_knowledge(query, limit=10)
        matches = []
        for r in res.get("results", []) or []:
            lib_name = r.get("source") or r.get("metadata", {}).get("library_name") or ""
            matches.append(
                _pkg._redact_result(
                    {
                        "library_id": f"rag:{_pkg._slugify(lib_name or r.get('id', ''))}",
                        "library_name": lib_name,
                        "language": r.get("metadata", {}).get("language", ""),
                        "ecosystem": r.get("metadata", {}).get("ecosystem", ""),
                        "verdict": "unknown",
                        "count": 1,
                        "source": r.get("source", ""),
                    },
                    ("library_name", "source"),
                )
            )
        return {
            "ok": True,
            "bridge": "rag_fallback",
            "tool": "resolve_library",
            "query": query,
            "matches": matches,
            "count": len(matches),
            "guard": guard,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("rag_fallback_resolve failed err=%s", str(exc)[:160])
        return {
            "ok": False,
            "bridge": "rag_fallback",
            "tool": "resolve_library",
            "query": query,
            "matches": [],
            "count": 0,
            "guard": guard,
            "error": str(exc)[:200],
        }


async def _fallback_codebase(
    query: str,
    *,
    library_id: str,
    limit: int,
    guard: dict[str, Any],
) -> dict[str, Any]:
    """Search RugBrain's coding KB for library snippets when MCP is down.

    Shapes results like the MCP ``query_codebase`` response (``results`` with
    ``code_block``/``title``/``verdict``/``snippet_type``), sourced from
    coding-knowledge hits. Guarded: content redacted before returning.
    """
    from rugbrain import mcp_bridge as _pkg

    try:
        from rugbrain.search_service import search_coding_knowledge  # lazy import

        res = await search_coding_knowledge(query, limit=limit)
        results = []
        for r in res.get("results", []) or []:
            results.append(
                _pkg._redact_result(
                    {
                        "id": r.get("id"),
                        "library_id": library_id,
                        "title": r.get("source", "rag"),
                        "code_block": r.get("code_answer") or r.get("content", ""),
                        "verdict": "unknown",
                        "snippet_type": "knowledge",
                        "relevance_score": r.get("similarity", 0.0),
                    },
                    ("code_block", "title"),
                )
            )
        return {
            "ok": True,
            "bridge": "rag_fallback",
            "tool": "query_codebase",
            "library_id": library_id,
            "query": query,
            "results": results[:limit],
            "count": len(results),
            "guard": guard,
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("rag_fallback_codebase failed err=%s", str(exc)[:160])
        return {
            "ok": False,
            "bridge": "rag_fallback",
            "tool": "query_codebase",
            "library_id": library_id,
            "query": query,
            "results": [],
            "count": 0,
            "guard": guard,
            "error": str(exc)[:200],
        }
