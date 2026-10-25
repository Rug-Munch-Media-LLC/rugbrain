"""Per-collection search over the EXISTING /api/v1/search path (no new index)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

import httpx

_LIST_KEYS = ("results", "hits", "data", "items", "matches")


class Searcher(Protocol):
    async def search(self, collection: str, query: str, limit: int) -> list[dict[str, Any]]: ...


def _api_key() -> str:
    key = os.getenv("RMI_RAG_API_KEY", "")
    if key:
        return key
    try:
        for line in Path("/srv/secrets/rugbrain.env").read_text(encoding="utf-8").splitlines():
            if line.startswith("RMI_RAG_API_KEY="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if isinstance(body, dict):
        for key in _LIST_KEYS:
            value = body.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []


class HttpSearch:
    """Calls RugBrain's own retrieval (embed + rerank), one collection at a time."""

    def __init__(self, base: str | None = None, timeout: float = 30.0) -> None:
        self.base = (base or os.getenv("RUGBRAIN_URL", "http://127.0.0.1:8012")).rstrip("/")
        self.timeout = timeout

    async def search(self, collection: str, query: str, limit: int) -> list[dict[str, Any]]:
        headers = {"Accept": "application/json", "X-API-Key": _api_key()}
        payload = {"query": query, "collections": [collection], "limit": limit, "rerank": True}
        async with httpx.AsyncClient(base_url=self.base, timeout=self.timeout) as client:
            resp = await client.post("/api/v1/search", json=payload, headers=headers)
            resp.raise_for_status()
            return _rows(resp.json())
