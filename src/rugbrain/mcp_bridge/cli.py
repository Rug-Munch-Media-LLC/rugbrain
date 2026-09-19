"""Minimal CLI: ``python -m rugbrain.mcp_bridge <tool> <json-or-query>``."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any


async def _cli_main(argv: list[str]) -> int:
    """Dispatch a single bridge tool from the command line."""
    from rugbrain import mcp_bridge as _pkg

    if len(argv) < 2:
        print(
            "usage: python -m rugbrain.mcp_bridge "
            "{status|search|trace|security|resolve|codebase|sync} [query-or-json]"
        )
        return 2
    tool = argv[1]
    arg = argv[2] if len(argv) > 2 else None

    result: dict[str, Any]
    if tool == "status":
        result = await _pkg.mcp_status()
    elif tool == "sync":
        limit = int(arg) if arg and arg.isdigit() else 100
        result = await _pkg.sync_security_patterns(limit=limit)
    elif tool in {"search", "security", "resolve"}:
        if not arg:
            print(f"{tool} requires a query")
            return 2
        if tool == "search":
            result = await _pkg.search_codebase(arg)
        elif tool == "security":
            result = await _pkg.get_security_context(arg)
        else:
            result = await _pkg.resolve_library(arg)
    elif tool == "codebase":
        if not arg:
            print("codebase requires 'library_id query'")
            return 2
        parts = arg.split(None, 1)
        library_id = parts[0]
        query = parts[1] if len(parts) > 1 else ""
        result = await _pkg.query_codebase(library_id, query)
    elif tool == "trace":
        if not arg:
            print("trace requires a function name")
            return 2
        result = await _pkg.trace_codebase(arg)
    else:
        print(f"unknown tool: {tool}")
        return 2

    print(json.dumps(result, default=str, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_cli_main(sys.argv)))
