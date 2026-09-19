"""Bridge configuration: env vars, runner paths, MCP tool registries.

Environment:
    RMI_MCP_DIR      — codebase MCP server dir (default /path/to/rmi-codebase-mcp)
    RMI_MCP_PYTHON   — interpreter with fastmcp+chromadb (default the x402 venv)
    RMI_MCP_TIMEOUT  — subprocess timeout seconds (default 90)
"""

from __future__ import annotations

import os
from pathlib import Path

MCP_DIR = os.getenv("RMI_MCP_DIR", "/path/to/rmi-codebase-mcp")
MCP_PYTHON = os.getenv("RMI_MCP_PYTHON", "/path/to/rmi-mcp-x402/.venv/bin/python")
MCP_TIMEOUT = float(os.getenv("RMI_MCP_TIMEOUT", "90"))

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_RUNNER = _SCRIPTS_DIR / "mcp_tool_runner.py"

# MCP tools that are async in codebase_server.py.
_ASYNC_TOOLS = {"search", "trace", "refresh", "status"}
# MCP tools that are sync in server.py.
_SYNC_TOOLS = {"security", "patterns"}
# MCP tools that are sync in codebase_server.py (Context7-style reference tools).
_SYNC_CODEBASE_TOOLS = {"resolve_library", "query_codebase"}


class BridgeUnavailableError(RuntimeError):
    """Raised when the MCP server cannot be reached by any bridge path."""
