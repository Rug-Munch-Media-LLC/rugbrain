"""Live Qdrant REST probe — the only network edge of `corpus_map`.

Every number reported comes from a call against the running server. If the
server cannot be reached the caller gets `QdrantUnreachable`, which becomes a
typed refusal — never an empty collection list that reads as "corpus is empty".
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import httpx

TS_KEYS = (
    "ingested_at",
    "indexed_at",
    "created_at",
    "published_at",
    "updated_at",
    "date",
    "timestamp",
    "ts",
)


class QdrantUnreachable(RuntimeError):
    """Qdrant did not answer (or answered unusably). Carries the reason."""


class CorpusProbe(Protocol):
    async def collections(self) -> list[str]: ...

    async def describe(self, name: str, samples: int) -> dict[str, Any]: ...


def default_url() -> str:
    url = os.getenv("QDRANT_URL") or os.getenv("QDRANT_HTTP_URL")
    if url:
        return url.rstrip("/")
    return f"http://127.0.0.1:{os.getenv('QDRANT_PORT', '6334')}"


def _dim(info: dict[str, Any]) -> int | None:
    params = ((info.get("result") or {}).get("config") or {}).get("params") or {}
    vectors = params.get("vectors")
    if isinstance(vectors, dict):
        if "size" in vectors:
            return int(vectors["size"])
        for value in vectors.values():
            if isinstance(value, dict) and "size" in value:
                return int(value["size"])
    return None


class HttpQdrantProbe:
    """Thin REST adapter; one client per call keeps it safe in any event loop."""

    def __init__(self, url: str | None = None, timeout: float = 8.0) -> None:
        self.url = (url or default_url()).rstrip("/")
        self.timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.url, timeout=self.timeout)

    async def collections(self) -> list[str]:
        try:
            async with self._client() as client:
                resp = await client.get("/collections")
                resp.raise_for_status()
                raw = (resp.json().get("result") or {}).get("collections") or []
        except Exception as exc:
            raise QdrantUnreachable(f"{self.url}: {type(exc).__name__}: {exc}") from exc
        return sorted(item["name"] for item in raw if not item["name"].startswith("_"))

    async def describe(self, name: str, samples: int = 3) -> dict[str, Any]:
        try:
            async with self._client() as client:
                info = (await client.get(f"/collections/{name}")).json()
                result = info.get("result") or {}
                points = result.get("points_count")
                if points is None:
                    counted = await client.post(
                        f"/collections/{name}/points/count", json={"exact": False}
                    )
                    points = ((counted.json().get("result") or {}).get("count")) or 0
                scroll = await client.post(
                    f"/collections/{name}/points/scroll",
                    json={"limit": samples, "with_payload": True, "with_vector": False},
                )
                if scroll.status_code >= 400:
                    raise QdrantUnreachable(f"{name}: scroll HTTP {scroll.status_code}")
                hits = (scroll.json().get("result") or {}).get("points") or []
        except QdrantUnreachable:
            raise
        except Exception as exc:
            raise QdrantUnreachable(f"{self.url}/{name}: {type(exc).__name__}: {exc}") from exc
        return {
            "dim": _dim(info),
            "points": int(points or 0),
            "samples": [h.get("payload") or {} for h in hits][:samples],
        }
