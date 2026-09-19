"""Public bridge tools: ``search_codebase`` and ``trace_codebase``.

Both sanitize their input for prompt injection before dispatch (``guard``
metadata on the response; refusal when nothing remains) and redact result
content for secret patterns before returning. They fall back to RugBrain's
own coding knowledge when the MCP is unreachable.
"""

from __future__ import annotations

import logging
from typing import Any

from rugbrain.mcp_bridge.config import BridgeUnavailableError

logger = logging.getLogger(__name__)


def _normalize_code_hit(item: dict[str, Any], *, kind: str) -> dict[str, Any]:
    """Normalize an rmi_search hit into the bridge result shape."""
    return {
        "kind": kind,
        "id": item.get("id"),
        "content": item.get("document") or item.get("content") or "",
        "score": float(item.get("score") or item.get("similarity") or 0.0),
        "metadata": item.get("metadata") or {},
    }


async def search_codebase(
    query: str,
    *,
    project: str | None = None,
    ecosystem: str | None = None,
    chain: str | None = None,
    code_type: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search the RMI codebase + security-pattern KB (MCP ``rmi_search``).

    Returns a normalized dict:
        {
          "ok", "bridge" ("mcp" | "rag_fallback"), "tool": "search_codebase",
          "query", "results": [{kind, id, content, score, metadata}], "total",
          "raw": <MCP payload when bridge=mcp>,
        }

    Falls back to RugBrain's own coding knowledge when the MCP is unreachable.

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
            "tool": "search_codebase",
            "query": query,
            "results": [],
            "total": 0,
            "guard": guard,
            "error": "query_refused_after_injection_sanitization",
        }
    try:
        raw = await _pkg._call_mcp(
            "search",
            {
                "query": safe_query,
                "project": project,
                "ecosystem": ecosystem,
                "chain": chain,
                "code_type": code_type,
                "limit": limit,
            },
        )
    except BridgeUnavailableError as exc:
        logger.info("search_codebase MCP unavailable → RAG fallback (%s)", str(exc)[:120])
        return await _pkg._fallback_search(query, limit=limit)

    results: list[dict[str, Any]] = []
    for item in raw.get("code_results", []) or []:
        results.append(_normalize_code_hit(item, kind="code"))
    for item in raw.get("security_patterns", []) or []:
        results.append(_normalize_code_hit(item, kind="security_pattern"))

    results = [
        _pkg._redact_result(r, ("content", "document", "code_answer", "title")) for r in results
    ]
    return {
        "ok": True,
        "bridge": "mcp",
        "tool": "search_codebase",
        "query": query,
        "results": results[:limit],
        "total": len(results),
        "raw": raw,
        "guard": guard,
    }


async def trace_codebase(
    function: str,
    *,
    project: str | None = None,
    direction: str = "both",
    depth: int = 3,
) -> dict[str, Any]:
    """Trace a function's call chain with security overlay (MCP ``rmi_trace``).

    Returns:
        {
          "ok", "bridge" ("mcp" | "rag_fallback"), "tool": "trace_codebase",
          "function", "trace", "security_flags", "backend",
        }

    Falls back to RugBrain's own coding knowledge when the MCP is unreachable.

    Guarded: the function symbol is sanitized for prompt injection before
    dispatch (``guard`` metadata on the response; refusal when nothing
    remains). Trace payload is redacted for secret patterns before returning.
    """
    from rugbrain import mcp_bridge as _pkg

    safe_function, guard = _pkg._guard_query(function)
    if guard["refused"]:
        return {
            "ok": False,
            "bridge": "guarded",
            "tool": "trace_codebase",
            "function": function,
            "trace": None,
            "security_flags": [],
            "backend": "unknown",
            "guard": guard,
            "error": "query_refused_after_injection_sanitization",
        }
    try:
        raw = await _pkg._call_mcp(
            "trace",
            {
                "function_name": safe_function,
                "project": project,
                "direction": direction,
                "depth": depth,
            },
        )
    except BridgeUnavailableError as exc:
        logger.info("trace_codebase MCP unavailable → RAG fallback (%s)", str(exc)[:120])
        return await _pkg._fallback_trace(function, depth=depth)

    trace = raw.get("trace")
    if isinstance(trace, dict):
        trace = _pkg._redact_result(trace, ("name", "file", "qualified_name", "signature"))
    elif isinstance(trace, str):
        trace = _pkg._redact_text(trace)
    security_flags = [
        _pkg._redact_result(f, ("warning", "name", "pattern_id"))
        for f in raw.get("security_flags", []) or []
    ]
    return {
        "ok": True,
        "bridge": "mcp",
        "tool": "trace_codebase",
        "function": function,
        "trace": trace,
        "security_flags": security_flags,
        "backend": raw.get("trace_backend", "unknown"),
        "guard": guard,
    }
