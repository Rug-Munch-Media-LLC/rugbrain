"""MCP availability probes: ``mcp_status`` and ``mcp_available``."""

from __future__ import annotations

from typing import Any

from rugbrain.mcp_bridge.config import BridgeUnavailableError


async def mcp_status() -> dict[str, Any]:
    """Return the MCP server's own status dashboard (rmi_status)."""
    from rugbrain import mcp_bridge as _pkg

    try:
        return await _pkg._call_mcp("status", {})
    except BridgeUnavailableError as exc:
        return {"ok": False, "bridge": "unavailable", "error": str(exc)}


async def mcp_available() -> bool:
    """Cheap liveness probe — True when the MCP status tool responds."""
    from rugbrain import mcp_bridge as _pkg

    try:
        status = await _pkg._call_mcp("status", {})
        return bool(status.get("ok") or status)
    except BridgeUnavailableError:
        return False
