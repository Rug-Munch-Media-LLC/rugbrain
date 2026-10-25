"""Minimal MCP client for the ``polymarket`` server (transport only).

Capabilities the gamma HTTP API cannot serve live are read here:
order-book depth and probability history. Odds/search live in
:mod:`rugbrain.polymarket_source`, which prefers the MCP and falls back.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

import httpx

MCP_URL = os.getenv("POLYMARKET_MCP_URL", "http://127.0.0.1:9109/mcp")
TIMEOUT = httpx.Timeout(8.0, connect=4.0)


def _sse_json(body: str) -> dict[str, Any]:
    for line in body.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except ValueError:
                continue
    return json.loads(body)


def http_mcp_call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Stateless MCP ``tools/call`` over streamable HTTP (JSON or SSE reply)."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.post(
            MCP_URL, json=payload, headers={"Accept": "application/json, text/event-stream"}
        )
    resp.raise_for_status()
    message = _sse_json(resp.text)
    if "error" in message:
        raise RuntimeError(f"mcp error: {message['error']}")
    result = message.get("result") or {}
    if result.get("isError"):
        raise RuntimeError(f"mcp tool {tool} returned isError")
    content = result.get("content") or []
    if content and isinstance(content[0], dict) and content[0].get("text"):
        try:
            return json.loads(content[0]["text"])
        except ValueError:
            return {"text": content[0]["text"]}
    return result


def order_book(
    token_id: str, *, mcp: Callable[..., dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Live depth — MCP only (gamma has no book)."""
    return (mcp or http_mcp_call)(
        "polymarket_get_order_book", {"token_id": token_id, "depth": 10}
    )


def price_history(
    token_id: str, interval: str = "1d", *, mcp: Callable[..., dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Probability time-series — MCP only (gamma has no history endpoint)."""
    return (mcp or http_mcp_call)(
        "polymarket_get_price_history", {"token_id": token_id, "interval": interval}
    )
