"""web_search — searxng first, pry MCP fallback, cited dates preserved."""

from __future__ import annotations

import os
from typing import Any

from .envelope import Envelope, ok_envelope, refuse
from .fetch import ProviderError, get_json
from .mcp import mcp_call


def _searxng_url() -> str:
    return os.getenv("LIVE_SEARXNG_URL", "http://127.0.0.1:8089/search")


def _pry_url() -> str:
    return os.getenv("LIVE_PRY_MCP_URL", "")


def _pry_tool() -> str:
    return os.getenv("LIVE_PRY_SEARCH_TOOL", "pry_search")


def _item(title: Any, url: Any, snippet: Any, published_at: Any, source: str) -> dict[str, Any]:
    return {
        "title": (title or "").strip(),
        "url": (url or "").strip(),
        "snippet": (snippet or "").strip()[:400],
        "published_at": published_at or None,
        "source": source,
    }


async def _from_searxng(query: str, n: int) -> tuple[list[dict[str, Any]], str]:
    payload, fetched = await get_json(
        _searxng_url(),
        provider="searxng",
        params={"q": query, "format": "json", "safesearch": 0},
    )
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ProviderError("searxng", "bad_payload", "no results list in searxng payload")
    items = [
        _item(r.get("title"), r.get("url"), r.get("content"), r.get("publishedDate"), "searxng")
        for r in rows[:n]
        if isinstance(r, dict) and r.get("url")
    ]
    return items, fetched


async def _from_pry(query: str, n: int) -> tuple[list[dict[str, Any]], str]:
    url = _pry_url()
    if not url:
        raise ProviderError("pry-mcp", "not_configured", "LIVE_PRY_MCP_URL is unset")
    payload, fetched = await mcp_call(
        url, _pry_tool(), {"query": query, "limit": n}, provider="pry-mcp"
    )
    rows = payload.get("results") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ProviderError("pry-mcp", "bad_payload", "no results list in pry payload")
    items = [
        _item(r.get("title"), r.get("url"), r.get("snippet") or r.get("content"), r.get("date"), "pry")
        for r in rows[:n]
        if isinstance(r, dict) and r.get("url")
    ]
    return items, fetched


async def web_search(q: str, n: int = 5) -> Envelope:
    """Cited web search. Typed refusal naming the provider when every source fails."""
    q = (q or "").strip()
    if not q:
        return refuse("bad_request", "query is required", "web_search")
    failures: list[str] = []
    for provider, fn in (("searxng", _from_searxng), ("pry-mcp", _from_pry)):
        try:
            items, fetched = await fn(q, max(1, n))
        except ProviderError as exc:
            failures.append(f"{exc.provider}:{exc.code}")
            continue
        if items:
            return ok_envelope(provider, items, fetched)
        failures.append(f"{provider}:empty")
    return refuse("all_providers_failed", "; ".join(failures), "web_search")
