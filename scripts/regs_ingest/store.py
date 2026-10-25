"""Vector-store access, existing-key scan, and retrieval probes."""

from __future__ import annotations

import inspect

import httpx

from rugbrain.qdrant_store import QdrantVectorStore
from regs_ingest.config import COLLECTION, KEY_FILE, QDRANT_URL, SF_EMBED_URL, SF_MODEL
from regs_ingest.normalise import canonical_url


class SiliconFlowEmbedder:
    """4096-d Qwen3-Embedding-8B adapter for the corpus lane."""

    def __init__(self) -> None:
        with open(KEY_FILE, encoding="utf-8") as fh:
            self._key = fh.read().strip()
        self._client = httpx.AsyncClient(timeout=60.0)

    async def embed_one(self, text: str) -> list[float]:
        resp = await self._client.post(
            SF_EMBED_URL,
            headers={"Authorization": f"Bearer {self._key}"},
            json={"model": SF_MODEL, "input": text, "encoding_format": "float"},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


def build_store():
    params = inspect.signature(QdrantVectorStore.__init__).parameters
    if "url" in params:
        return QdrantVectorStore(url=QDRANT_URL)
    if "host" in params:
        return QdrantVectorStore(host="127.0.0.1", port=6334)
    return QdrantVectorStore()


async def existing_keys(max_payloads: int = 20000) -> tuple[set[str], dict]:
    """Canonical URLs already in the collection + honest payload-shape report."""
    urls: set[str] = set()
    keys: set[str] = set()
    seen = 0
    offset = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        while seen < max_payloads:
            body = {"limit": 512, "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            resp = await client.post(f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll", json=body)
            if resp.status_code != 200:
                return urls, {"error": f"HTTP {resp.status_code}", "scan_urls": len(urls)}
            result = resp.json()["result"]
            points = result.get("points", [])
            for point in points:
                payload = point.get("payload") or {}
                keys.update(payload.keys())
                raw = payload.get("url") or (payload.get("metadata") or {}).get("canonical_url")
                if raw:
                    urls.add(canonical_url(raw))
            seen += len(points)
            offset = result.get("next_page_offset")
            if not points or offset is None:
                break
    return urls, {"payloads": seen, "urls": len(urls), "has_url_key": "url" in keys,
                  "keys": sorted(keys)[:14]}


async def search(embedder, query: str, collection: str, k: int = 3) -> list[dict]:
    vec = await embedder.embed_one(query)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{QDRANT_URL}/collections/{collection}/points/search",
                json={"vector": vec, "limit": k, "with_payload": True},
            )
            resp.raise_for_status()
            hits = resp.json()["result"]
    except Exception as exc:  # unavailable collection: fail-open for the report
        return [{"score": 0.0, "source": f"ERROR {type(exc).__name__}: {str(exc)[:60]}",
                 "licence": "-", "text": ""}]
    return [{
        "score": round(h["score"], 4),
        "source": (h["payload"].get("source") or "?")[:40],
        "licence": h["payload"].get("licence", "-"),
        "text": (h["payload"].get("content") or h["payload"].get("text") or "")[:150],
    } for h in hits]


async def probe(embedder, queries: list[str], collections: list[str]) -> dict:
    out = {}
    for query in queries:
        out[query] = {c: await search(embedder, query, c) for c in collections}
    return out


async def count_points(collection: str = COLLECTION) -> int:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{QDRANT_URL}/collections/{collection}")
        return resp.json()["result"].get("points_count", -1)
