#!/usr/bin/env python3
"""Execute an RMI Codebase MCP tool and print the JSON result to stdout.

Spawned by ``rugbrain.mcp_bridge`` as a subprocess under an interpreter that
has ``fastmcp`` + ``chromadb`` installed (see ``RMI_MCP_PYTHON``). The runner
imports the MCP server modules from ``RMI_MCP_DIR``, calls the requested tool
with JSON kwargs from argv, and prints ``json.dumps(result, default=str)`` to
stdout so the bridge can read it back. All MCP logging goes to stderr.

Usage:
    python mcp_tool_runner.py <tool> '<json-kwargs>'

Supported tools:
    search            — codebase_server.rmi_search (async)
    trace             — codebase_server.rmi_trace (async)
    refresh           — codebase_server.rmi_refresh (async)
    status            — codebase_server.rmi_status (async)
    resolve_library   — codebase_server.resolve_library (sync)
    query_codebase    — codebase_server.query_codebase (sync)
    security          — server.query_security_context (sync)
    patterns          — server.list_patterns (sync)

No secrets: tool kwargs come from the caller; results are printed only to
stdout and never logged.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any


def _mcp_dir() -> str:
    return os.environ.get("RMI_MCP_DIR", "/srv/work/repos/rmi-codebase-mcp")


def _result(data: dict[str, Any]) -> None:
    """Print *data* as a single JSON line on stdout."""
    print(json.dumps(data, default=str), flush=True)


async def _call_async(fn: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Run an async MCP tool inside the server lifespan (starts codebase-memory)."""
    import codebase_server as cs

    async with cs.lifespan(None):  # type: ignore[arg-type]
        result = await fn(**kwargs)
    return result


def main() -> int:
    if len(sys.argv) < 2:
        _result({"ok": False, "error": "missing tool name"})
        return 2
    tool = sys.argv[1]
    try:
        kwargs = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    except ValueError:
        _result({"ok": False, "error": "invalid JSON kwargs"})
        return 2

    sys.path.insert(0, _mcp_dir())

    try:
        if tool in {"search", "trace", "refresh", "status"}:
            import codebase_server as cs

            fn = {
                "search": cs.rmi_search,
                "trace": cs.rmi_trace,
                "refresh": cs.rmi_refresh,
                "status": cs.rmi_status,
            }[tool]
            result = asyncio.run(_call_async(fn, kwargs))
        elif tool in {"resolve_library", "query_codebase"}:
            import codebase_server as cs

            # Sync Context7-style reference tools — no lifespan needed.
            fn = {
                "resolve_library": cs.resolve_library,
                "query_codebase": cs.query_codebase,
            }[tool]
            result = fn(**kwargs)
        elif tool == "security":
            import server as srv

            result = srv.query_security_context(**kwargs)
        elif tool == "patterns":
            import server as srv

            result = srv.list_patterns(**kwargs)
        else:
            _result({"ok": False, "error": f"unknown tool: {tool}"})
            return 2
    except Exception as exc:  # pragma: no cover - defensive
        _result({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1

    _result({"ok": True, "tool": tool, "result": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
