"""Multi-embedder federation — RAG reads (and writes) across ALL backends.

Backends in the fleet (2026-08-24 directive: "able to read all methods"):
    qwen4b      local llama.cpp :8004  (deep lane; server serves MRL-truncated
                                        vectors — dim comes from env/config)
    qwen06b     local llama.cpp :8007  (fast lane, 1024-dim)
    nemotron    OpenRouter nvidia/nemotron-3-embed-1b:free (2048-dim, $0)
    openrouter  any OpenRouter embeddings model (paid, e.g. qwen3-embedding-4b)

Why federation: vectors from different models are NOT comparable and cannot
mix inside one collection (TOP-100 #25). But the CORPUS legitimately spans
multiple collections built by different backends over time (canonical v2,
interim _i shadow namespaces, speed-lane collections). A reader that only
speaks ONE backend is blind to the rest.

Federated search:
    1. Registry maps each collection -> (backend, dim) via
       RAG_COLLECTION_BACKENDS env ("coll=backend:dim,...") with sane
       defaults derived from the collection name.
    2. A fan-out query embeds the query text ONCE PER DISTINCT BACKEND,
       searches its collections, then merges results (cosine scores are
       comparable enough across models for rank-fusion purposes; final
       ordering uses RRF-style reciprocal ranking to avoid raw-score bias).
    3. Every hit is tagged with its backend so downstream consumers know
       provenance.

Ingest side: callers pick a backend explicitly (or default); the store's
per-collection ensure/create handles whatever dim results.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("rugbrain.multi_embedder")

# ── Backend definitions ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Backend:
    name: str
    kind: str  # "llamacpp" | "openrouter"
    url: str  # completions-compatible embeddings endpoint base
    dim: int
    model: str = ""
    api_key_env: str = ""


def _default_backends() -> dict[str, Backend]:
    # Registry-aware: native dim of the configured model beats stale hardcoded
    # 1024 (qwen3-embedding-4b serves native 2560 on :8004 since 2026-08-24).
    from rugbrain.dim_policy import default_dim_for_model

    _model = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")
    q4_dim = int(
        os.getenv("QWEN_EMBED_DIMS") or os.getenv("RAG_EMBED_DIM") or default_dim_for_model(_model)
    )
    return {
        "qwen4b": Backend(
            "qwen4b",
            "llamacpp",
            os.getenv("QWEN_EMBED_URL", "http://172.22.0.1:8004/v1"),
            q4_dim,
            model=os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b"),
        ),
        "qwen06b": Backend(
            "qwen06b",
            "llamacpp",
            os.getenv("QWEN_SPEED_URL", "http://172.22.0.1:8007/v1"),
            int(os.getenv("QWEN_SPEED_DIMS", "1024")),
            model=os.getenv("RAG_EMBED_SPEED_MODEL", "qwen3-embedding-0.6b"),
        ),
        "nemotron": Backend(
            "nemotron",
            "openrouter",
            os.getenv("OPENROUTER_EMBED_URL", "https://openrouter.ai/api/v1/embeddings"),
            int(os.getenv("RAG_NEMOTRON_DIMS", "2048")),
            model=os.getenv("RAG_OPENROUTER_FREE_MODEL", "nvidia/nemotron-3-embed-1b:free"),
            api_key_env="OPENROUTER_API_KEY",
        ),
    }


def _parse_collection_map() -> dict[str, tuple[str, int]]:
    """RAG_COLLECTION_BACKENDS='news_v2=qwen4b:1024,news_v2_i=nemotron:2048,...'"""
    out: dict[str, tuple[str, int]] = {}
    raw = os.getenv("RAG_COLLECTION_BACKENDS", "")
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        coll, spec = part.split("=", 1)
        try:
            bname, dim = spec.rsplit(":", 1)
            out[coll.strip()] = (bname.strip(), int(dim))
        except ValueError:
            logger.warning("bad backend map entry: %s", part)
    return out


class MultiEmbedder:
    """Embeds text via any registered backend; federates searches."""

    def __init__(self) -> None:
        self.backends = _default_backends()
        self.collection_map = _parse_collection_map()
        # sensible defaults when a collection isn't mapped
        self.default_backend = os.getenv("RAG_DEFAULT_BACKEND", "qwen4b")

    def backend_for(self, collection: str) -> tuple[Backend, int]:
        if collection in self.collection_map:
            bname, dim = self.collection_map[collection]
            be = self.backends.get(bname)
            if be is None:
                raise KeyError(f"unknown backend '{bname}' for {collection}")
            return be, dim
        be = self.backends[self.default_backend]
        return be, be.dim

    async def embed(self, text: str, backend_name: str) -> list[float]:
        be = self.backends[backend_name]
        headers: dict[str, str] = {}
        if be.api_key_env:
            key = os.getenv(be.api_key_env, "")
            if key:
                headers["Authorization"] = f"Bearer {key}"
        async with httpx.AsyncClient(timeout=45.0) as c:
            r = await c.post(
                be.url,
                json={"model": be.model, "input": text[:8000]},
                headers=headers,
            )
            r.raise_for_status()
            data: list[dict[str, Any]] = r.json()["data"]
            vec: list[float] = data[0]["embedding"]
        # normalize to the backend's declared dim (truncate-down only)
        if len(vec) > be.dim:
            cut = vec[: be.dim]
            norm = sum(x * x for x in cut) ** 0.5
            vec = [x / norm for x in cut] if norm else cut
        return vec

    async def federated_search(
        self,
        query: str,
        collections: list[str],
        limit_per: int = 8,
        min_similarity: float = 0.0,
        qdrant_url: str = "",
    ) -> list[dict[str, Any]]:
        """Embed once per distinct backend, search each collection, RRF-merge."""
        from collections import defaultdict

        groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for coll in collections:
            be, dim = self.backend_for(coll)
            groups[be.name].append((coll, dim))

        async def _one_backend(bname: str, colls: list[tuple[str, int]]) -> list[dict[str, Any]]:
            try:
                vec = await self.embed(query, bname)
            except Exception as e:
                logger.warning("fed_embed_failed backend=%s err=%s", bname, str(e)[:80])
                return []
            hits: list[dict[str, Any]] = []
            async with httpx.AsyncClient(timeout=20.0) as c:
                for coll, _dim in colls:
                    body: dict[str, Any] = {
                        "vector": vec,
                        "limit": limit_per,
                        "with_payload": True,
                    }
                    try:
                        r = await c.post(
                            f"{qdrant_url}/collections/{coll}/points/search", json=body
                        )
                        if r.status_code != 200:
                            continue
                        for p in r.json().get("result", []):
                            score = float(p.get("score", 0))
                            if score < min_similarity:
                                continue
                            pl = p.get("payload", {}) or {}
                            hits.append(
                                {
                                    "content": pl.get("content", ""),
                                    "metadata": pl.get("metadata", {}),
                                    "source": pl.get("source", ""),
                                    "severity": pl.get("severity", ""),
                                    "score": score,
                                    "collection": coll,
                                    "backend": bname,
                                }
                            )
                    except Exception as e:
                        logger.debug("fed_search_failed coll=%s err=%s", coll, str(e)[:60])
            return hits

        results = await asyncio.gather(*(_one_backend(b, cs) for b, cs in groups.items()))

        # Reciprocal-rank fusion across backend groups (avoids raw-score bias)
        k = 60
        rrf: dict[str, float] = defaultdict(float)
        best: dict[str, dict[str, Any]] = {}
        for lst in results:
            for rank, h in enumerate(lst):
                key = (h.get("content") or "")[:160]
                if not key:
                    continue
                rrf[key] += 1.0 / (k + rank + 1)
                if key not in best or h["score"] > best[key]["score"]:
                    best[key] = h
        merged = []
        for key, s in sorted(rrf.items(), key=lambda kv: -kv[1]):
            item = dict(best[key])
            item["rrf"] = round(s, 6)
            merged.append(item)
        return merged


_singleton: MultiEmbedder | None = None


def get_multi_embedder() -> MultiEmbedder:
    global _singleton
    if _singleton is None:
        _singleton = MultiEmbedder()
    return _singleton
