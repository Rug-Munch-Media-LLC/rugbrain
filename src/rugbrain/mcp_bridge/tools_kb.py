"""Public bridge tools over the security/reference MCP surface:
``get_security_context``, ``resolve_library``, ``query_codebase``.

All sanitize their input for prompt injection before dispatch, redact
result content for secret patterns before returning, and fall back to
RugBrain's own knowledge when the MCP is unreachable.
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.mcp_bridge.config import BridgeUnavailableError

logger = logging.getLogger(__name__)


async def get_security_context(
    query: str,
    *,
    top_k: int = 5,
    filter_severity: str | None = None,
    filter_ecosystem: str | None = None,
    filter_category: str | None = None,
    format: str = "compact",
) -> dict[str, Any]:
    """Query the Web3 security-pattern KB (MCP ``query_security_context``).

    Returns:
        {
          "ok", "bridge" ("mcp" | "rag_fallback"), "tool": "get_security_context",
          "query", "results": [{id, title, description, relevance_score, metadata, code_block?}],
          "count",
        }

    Falls back to RugBrain's own scam/coding knowledge when the MCP is unreachable.

    Guarded: the query is sanitized for prompt injection before dispatch
    (``guard`` metadata on the response; refusal when nothing remains).
    Result content is redacted for secret patterns before returning.
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
        raw = await _pkg._call_mcp(
            "security",
            {
                "query": safe_query,
                "top_k": top_k,
                "filter_severity": filter_severity,
                "filter_ecosystem": filter_ecosystem,
                "filter_category": filter_category,
                "format": format,
            },
        )
    except BridgeUnavailableError as exc:
        logger.info("get_security_context MCP unavailable → RAG fallback (%s)", str(exc)[:120])
        return await _pkg._fallback_security(query, top_k=top_k)

    results = raw.get("results", []) or []
    results = [
        _pkg._redact_result(r, ("description", "code_block", "title", "content")) for r in results
    ]
    return {
        "ok": bool(raw.get("success", True)),
        "bridge": "mcp",
        "tool": "get_security_context",
        "query": query,
        "results": results[:top_k],
        "count": len(results),
        "guard": guard,
    }


async def resolve_library(query: str) -> dict[str, Any]:
    """Resolve a crypto library name to known library_ids (MCP ``resolve_library``).

    Returns:
        {
          "ok", "bridge" ("mcp" | "rag_fallback"), "tool": "resolve_library",
          "query", "matches": [{library_id, library_name, language,
                                ecosystem, verdict, count}], "count", "guard",
        }

    Falls back to RugBrain's own coding knowledge when the MCP is unreachable.
    Guarded: the query is sanitized for prompt injection before dispatch.
    """
    from rugbrain import mcp_bridge as _pkg

    safe_query, guard = _pkg._guard_query(query)
    if guard["refused"]:
        return {
            "ok": False,
            "bridge": "guarded",
            "tool": "resolve_library",
            "query": query,
            "matches": [],
            "count": 0,
            "guard": guard,
            "error": "query_refused_after_injection_sanitization",
        }
    try:
        raw = await _pkg._call_mcp("resolve_library", {"query": safe_query})
    except BridgeUnavailableError as exc:
        logger.info("resolve_library MCP unavailable → RAG fallback (%s)", str(exc)[:120])
        return await _pkg._fallback_resolve(query, guard=guard)

    matches = raw.get("matches", []) or []
    matches = [_pkg._redact_result(m, ("library_name",)) for m in matches]
    return {
        "ok": bool(raw.get("success", True)),
        "bridge": "mcp",
        "tool": "resolve_library",
        "query": query,
        "matches": matches,
        "count": len(matches),
        "guard": guard,
    }


async def query_codebase(
    library_id: str,
    query: str,
    *,
    filter_verdict: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search code snippets inside a specific crypto library (MCP ``query_codebase``).

    Returns:
        {
          "ok", "bridge" ("mcp" | "rag_fallback"), "tool": "query_codebase",
          "library_id", "query", "results": [{id, library_id, title,
            code_block, verdict, snippet_type, relevance_score}], "count",
          "guard",
        }

    Falls back to RugBrain's own coding knowledge when the MCP is unreachable.
    Guarded: the query is sanitized for prompt injection before dispatch.
    Result content (``code_block`` / ``title``) is redacted for secret
    patterns before returning.
    """
    from rugbrain import mcp_bridge as _pkg

    safe_query, guard = _pkg._guard_query(query)
    if guard["refused"]:
        return {
            "ok": False,
            "bridge": "guarded",
            "tool": "query_codebase",
            "library_id": library_id,
            "query": query,
            "results": [],
            "count": 0,
            "guard": guard,
            "error": "query_refused_after_injection_sanitization",
        }
    try:
        raw = await _pkg._call_mcp(
            "query_codebase",
            {
                "library_id": library_id,
                "query": safe_query,
                "filter_verdict": filter_verdict,
                "limit": min(limit, 100),
            },
        )
    except BridgeUnavailableError as exc:
        logger.info("query_codebase MCP unavailable → RAG fallback (%s)", str(exc)[:120])
        return await _pkg._fallback_codebase(query, library_id=library_id, limit=limit, guard=guard)

    results = raw.get("results", []) or []
    results = [_pkg._redact_result(r, ("code_block", "title")) for r in results]
    return {
        "ok": bool(raw.get("success", True)),
        "bridge": "mcp",
        "tool": "query_codebase",
        "library_id": library_id,
        "query": query,
        "results": results[:limit],
        "count": len(results),
        "guard": guard,
    }
