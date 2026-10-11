"""Multi-embedder federation — RAG reads (and writes) across ALL backends.

Backends in the fleet:
    deepinfra   DeepInfra Qwen/Qwen3-Embedding-8B (4096-dim) — CANONICAL
                primary. Same model as local ⇒ same vector space.
    qwen8b      local llama.cpp :8004 (canonical FALLBACK). The KEY is a
                historical lane identifier kept for RAG_COLLECTION_BACKENDS
                mappings; the model it serves is 8B.
    nemotron    OpenRouter nvidia/nemotron-3-embed-1b:free (2048-dim) —
                LEGACY READS ONLY (interim `_i` collections).
    or_qwen8b   OpenRouter lane kept only for old RAG_COLLECTION_BACKENDS
                mappings naming or_qwen8b.

CANONICAL EMBEDDER (one, for all new writes):
    qwen3-embedding-8b @ 4096-dim, DeepInfra primary + local :8004 fallback
    (EMBED_CHAINS["qwen8b"]). Both lanes are the SAME model, so the vector
    space is identical regardless of which lane serves the request. The
    retired 4B/2560 generation is eliminated: no backend defaults to it.

Why federation survives: vectors from different models are NOT comparable
and cannot mix inside one collection. But the CORPUS legitimately spans
multiple collections built by different backends over time (canonical 4096,
interim _i 2048, legacy 1024), so legacy backends stay registered for READS
while every WRITE goes through the canonical chain.

Federated search:
    1. Registry maps each collection -> (backend, dim) via
       RAG_COLLECTION_BACKENDS env ("coll=backend:dim,...").
    2. A fan-out query embeds the query text ONCE PER DISTINCT BACKEND,
       searches its collections, then merges results (final ordering uses
       RRF-style reciprocal ranking to avoid raw-score bias).
    3. Every hit is tagged with its backend so downstream consumers know
       provenance.

Ingest side: callers pick a backend explicitly (or default); the store's
per-collection ensure/create handles whatever dim results. New collections
MUST be created at the canonical dim (see ``rugbrain.dim_policy``).
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
    kind: str  # "llamacpp" | "deepinfra" | "openrouter"
    url: str  # completions-compatible embeddings endpoint base
    dim: int
    model: str = ""
    api_key_env: str = ""


# Secrets-file fallback for the DeepInfra lane (env var wins). Same pattern
# as rmi-codebase-mcp/scripts/deepinfra_embed.py; override path via env.
_DEEPINFRA_KEY_FILE = os.getenv("DEEPINFRA_KEY_FILE", "/run/secrets/deepinfra_api_key")


def _load_api_key(env_var: str = "DEEPINFRA_API_KEY") -> str:
    """API key for a remote backend: env var first, secrets-file fallback.

    Only ``DEEPINFRA_API_KEY`` has a file fallback
    (``DEEPINFRA_KEY_FILE`` — pattern from
    rmi-codebase-mcp/scripts/deepinfra_embed.py). Any other env name returns
    "" when unset: callers treat an empty key as "no auth header" and the
    provider chain simply falls through to the next lane (no raise — a
    missing OpenRouter key must not take down the local lanes).
    """
    val = os.getenv(env_var, "").strip()
    if val:
        return val
    if env_var == "DEEPINFRA_API_KEY":
        # Try every known key location: explicit env path, host path, then
        # the container mount (/run/secrets) — 2026-09-14: only the host
        # path exists on Talos, so /run/secrets-only lookups fell to local.
        for path in (
            _DEEPINFRA_KEY_FILE,
            "/srv/secrets/deepinfra_api_key",
            "/run/secrets/deepinfra_api_key",
        ):
            try:
                with open(path, encoding="utf-8") as fh:
                    key = fh.read().strip()
                if key:
                    return key
            except OSError:
                continue
        logger.warning("deepinfra_key_unavailable env=%s tried=%s", env_var, _DEEPINFRA_KEY_FILE)
    return ""


def _default_backends() -> dict[str, Backend]:
    # The retired 4B/2560 generation is eliminated: no lane here may default
    # to it. Dim comes from the registry, not a literal, so the lane switch
    # moves it in one place. QWEN_EMBED_DIMS stays an explicit override; the
    # legacy global RAG_EMBED_DIM (2048, nemotron era) lied about this lane and
    # no longer participates.
    from rugbrain.dim_policy import default_dim_for_model

    _model = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b")
    lane_dim = int(os.getenv("QWEN_EMBED_DIMS") or default_dim_for_model(_model))
    return {
        # CANONICAL PRIMARY: DeepInfra hosts the same qwen3-embedding-8b the
        # local :8004 serves — 4096-dim, one vector space, always warm.
        "deepinfra": Backend(
            "deepinfra",
            "deepinfra",
            os.getenv("DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1/embeddings"),
            lane_dim,
            model=os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B"),
            api_key_env="DEEPINFRA_API_KEY",
        ),
        # CANONICAL FALLBACK. The KEY stays "qwen8b": it is the lane identifier
        # RAG_COLLECTION_BACKENDS mappings and the test contract name, and
        # renaming a registry key would break those without eliminating
        # anything. The MODEL it serves is the canonical 8B lane.
        "qwen8b": Backend(
            "qwen8b",
            "llamacpp",
            os.getenv("QWEN_EMBED_URL", "http://127.0.0.1:8004/v1"),
            lane_dim,
            model=os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b"),
        ),
        # LEGACY READS ONLY — interim 2048 `_i` shadow collections.
        "nemotron": Backend(
            "nemotron",
            "openrouter",
            os.getenv("OPENROUTER_EMBED_URL", "https://openrouter.ai/api/v1/embeddings"),
            int(os.getenv("RAG_NEMOTRON_DIMS", "2048")),
            model=os.getenv("RAG_OPENROUTER_FREE_MODEL", "nvidia/nemotron-3-embed-1b:free"),
            api_key_env="OPENROUTER_API_KEY",
        ),
        # LEGACY remote lane (kept ONLY so old RAG_COLLECTION_BACKENDS entries
        # naming or_qwen8b still resolve), serving the canonical model so a
        # legacy mapping cannot reintroduce a 4B vector.
        "or_qwen8b": Backend(
            "or_qwen8b",
            "openrouter",
            os.getenv("OR_QWEN_EMBED_URL", "https://openrouter.ai/api/v1/embeddings"),
            lane_dim,
            model=os.getenv("OR_QWEN_EMBED_MODEL", "qwen/qwen3-embedding-8b"),
            api_key_env="OPENROUTER_API_KEY",
        ),
    }


# Provider chains per backend, in try-order. Every lane inside a chain
# serves the SAME model family ⇒ same vector space ⇒ results stay
# comparable with the target collection (no cross-family fallback, ever).
# Canonical (2026-08-29): DeepInfra primary, local :8004 fallback.
EMBED_CHAINS: dict[str, tuple[str, ...]] = {
    "qwen8b": ("deepinfra", "qwen8b"),
    "deepinfra": ("deepinfra", "qwen8b"),
    "or_qwen8b": ("or_qwen8b", "qwen8b"),
}

# Backends that hit a remote HTTP API (used by RAG_QUERY_REMOTE_FALLBACK=0
# to strip remote lanes and force a fully-local chain).
_REMOTE_BACKENDS = frozenset({"deepinfra", "or_qwen8b", "nemotron"})

# Deprecated alias (pre-2026-08-29 name). Kept so any external importer or
# ops script referencing it doesn't break; new code uses EMBED_CHAINS.
SAME_SPACE_FALLBACKS = EMBED_CHAINS


def _parse_collection_map() -> dict[str, tuple[str, int]]:
    """RAG_COLLECTION_BACKENDS='news_v2=qwen8b:1024,news_v2_i=nemotron:2048,...'"""
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
        self.default_backend = os.getenv("RAG_DEFAULT_BACKEND", "qwen8b")
        # Actual-dim awareness: lazily fetched from Qdrant per unknown
        # collection, cached for the process lifetime. This is what makes
        # the reader recognize ANY vector space, not just mapped ones.
        self._dim_cache: dict[str, int | None] = {}
        # collections whose vectors are NAMED (e.g. {"dense": [...]}) —
        # their search bodies must use {"vector": {"dense": vec}}.
        self._named_vector_collections: set[str] = set()

    @staticmethod
    def _qdrant_rest_urls() -> list[str]:
        """REST endpoints to probe for actual collection dims (deduped).

        Qdrant :6333 = REST, :6334 = gRPC — only REST is probed here.
        """
        urls: list[str] = []
        env_urls = os.getenv(
            "RAG_QDRANT_URLS",
            f"{os.getenv('QDRANT_URL', 'http://rmi-qdrant-v2:6334')},http://127.0.0.1:6333,http://127.0.0.1:6334",
        )
        for u in env_urls.split(","):
            u = u.strip().rstrip("/")
            # REST ports: container view :6333, host view :6334 (2026-09-14).
            if u and ":" in u and u not in urls and (u.endswith(":6333") or u.endswith(":6334")):
                urls.append(u)
        return urls or ["http://rmi-qdrant-v2:6334"]

    async def actual_dim(self, collection: str) -> int | None:
        """ACTUAL vector dim of *collection* from Qdrant (cached)."""
        if collection in self._dim_cache:
            return self._dim_cache[collection]
        dim: int | None = None
        import httpx

        for base in self._qdrant_rest_urls():
            try:
                async with httpx.AsyncClient(timeout=6) as c:
                    d = (await c.get(f"{base}/collections/{collection}")).json().get("result", {})
                    cfg = d.get("config", {}).get("params", {}).get("vectors", {})
                    if isinstance(cfg, dict) and "dense" in cfg:
                        dim = cfg["dense"].get("size")
                        self._named_vector_collections.add(collection)
                    elif isinstance(cfg, dict):
                        dim = cfg.get("size")
                    if isinstance(dim, int):
                        break
            except Exception:
                continue
        self._dim_cache[collection] = dim
        return dim

    def _backend_for_dim(self, dim: int | None) -> tuple[Backend, int]:
        """Pick the backend whose native space matches an ACTUAL dim.

        The canonical lane's dim is read from the backend registry, not
        written as a literal. The old ``if dim == 2560`` branch was an
        executable 4B path: after the retired collections were deleted, a 2560
        collection could only arrive here via a B2 restore, and the branch
        returned the 4096 lane while REPORTING 4096 — a silent width lie.

        The returned ``dim`` is always the chosen BACKEND's native width, so
        it can never claim a width that backend cannot produce. A dim match is
        therefore a hard requirement: a 4096 collection is never paired with
        the ambient 2048 nemotron default.
        """
        canonical = self.backends["deepinfra"].dim
        if dim == canonical:
            for be in self.backends.values():
                if be.dim == canonical:
                    return be, be.dim
            be = self.backends[self.default_backend]
            return be, be.dim
        if dim == 2048:
            be = self.backends["nemotron"]
            return be, be.dim
        # (No 1024 special case: those legacy collections were removed
        # 2026-09-14 — falls through to the default backend.)
        be = self.backends[self.default_backend]
        return be, be.dim

    def backend_for(self, collection: str) -> tuple[Backend, int]:
        if collection in self.collection_map:
            bname, dim = self.collection_map[collection]
            be = self.backends.get(bname)
            if be is None:
                raise KeyError(f"unknown backend '{bname}' for {collection}")
            return be, dim
        # Unmapped: resolve by ACTUAL dim when we've seen it (sync path uses
        # the cache only; federated_search pre-warms via resolve_collections).
        cached = self._dim_cache.get(collection)
        if cached is not None:
            return self._backend_for_dim(cached)
        be = self.backends[self.default_backend]
        return be, be.dim

    async def resolve_collections(self, collections: list[str]) -> list[str]:
        """Pre-warm actual-dim cache so backend_for() resolves by reality."""
        await asyncio.gather(*(self.actual_dim(c) for c in collections))
        return collections

    async def embed(
        self, text: str, backend_name: str, target_dim: int | None = None
    ) -> list[float]:
        """Embed via the provider chain for *backend_name*, falling back
        within the SAME model family (DeepInfra ⇄ local :8004 for the
        canonical 2560 space) so the vector stays comparable with the
        target collection's space.

        ``target_dim`` pins the output to an exact collection dim (MRL
        truncate-down, e.g. 1024 legacy collections on the native-2560
        model). Set RAG_QUERY_REMOTE_FALLBACK=0 to strip remote lanes and
        force a fully-local chain (local :8004 only for the qwen8b space).
        """
        if os.getenv("RAG_QUERY_REMOTE_FALLBACK", "1") == "0":
            chain = tuple(
                n
                for n in EMBED_CHAINS.get(backend_name, (backend_name,))
                if n not in _REMOTE_BACKENDS
            ) or (backend_name,)
        else:
            chain = EMBED_CHAINS.get(backend_name, (backend_name,))
        tried: list[str] = []
        last_err: Exception | None = None
        for name in chain:
            if name not in self.backends:
                continue
            tried.append(name)
            try:
                return await self._embed_once(text, self.backends[name], target_dim)
            except Exception as e:
                last_err = e
                logger.warning(
                    "embed_failed backend=%s err=%r — trying fallback",
                    name,
                    repr(e)[:120],
                )
        raise RuntimeError(f"all embed backends failed for '{backend_name}' {tried}: {last_err}")

    async def _embed_once(
        self, text: str, be: Backend, target_dim: int | None = None
    ) -> list[float]:
        headers: dict[str, str] = {}
        if be.api_key_env:
            key = _load_api_key(be.api_key_env)
            if key:
                headers["Authorization"] = f"Bearer {key}"
        timeout = float(os.getenv("RAG_EMBED_TIMEOUT", "20"))
        # URL conventions vary by backend: llamacpp entries end at /v1 while
        # OpenRouter entries carry the full /embeddings path. Normalize.
        url = (
            be.url
            if be.url.rstrip("/").endswith("/embeddings")
            else be.url.rstrip("/") + "/embeddings"
        )
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(
                url,
                json={"model": be.model, "input": text[:8000]},
                headers=headers,
            )
            r.raise_for_status()
            data: list[dict[str, Any]] = r.json()["data"]
            vec: list[float] = data[0]["embedding"]
        # normalize to the target collection dim (MRL truncate-down only);
        # falls back to the backend's declared native dim.
        out_dim = target_dim or be.dim
        if len(vec) > out_dim:
            cut = vec[:out_dim]
            norm = sum(x * x for x in cut) ** 0.5
            vec = [x / norm for x in cut] if norm else cut
        return vec

    async def landscape(self, qdrant_urls: list[str] | None = None) -> dict[str, Any]:
        """Live introspection of the ENTIRE vector landscape.

        Reads every collection's ACTUAL dim + point count from each Qdrant
        instance, infers the producing backend from the dim registry, and
        flags mismatches against this MultiEmbedder's configured resolution.
        This is the human/agent-readable answer to 'what vectors do we have
        and can we query them all?'
        """
        import time

        urls = qdrant_urls or [
            u.strip()
            for u in os.getenv(
                "RAG_QDRANT_URLS",
                # Container-aware defaults: prefer the service-network Qdrant
                # (legacy :6333 + v2 :6334 both live behind rmi-qdrant-v2's
                # host publishes); 127.0.0.1 only works for host-side callers.
                "http://rmi-qdrant-v2:6334,http://rmi-qdrant-v2:6334,"
                "http://127.0.0.1:6333,http://127.0.0.1:6334",
            ).split(",")
            if u.strip()
        ]
        t0 = time.monotonic()

        def _infer(dim: int | None) -> str:
            if dim == 2560:
                return "qwen8b"
            if dim == 2048:
                return "nemotron"
            if dim == 1024:
                return "legacy-1024 (retired lane)"
            if dim is None:
                return "unnamed/multivector-config"
            return f"unknown-dim-{dim}"

        out: dict[str, Any] = {
            "instances": {},
            "mismatches": [],
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        total = 0
        for url in urls:
            cols: dict[str, Any] = {}
            try:
                async with httpx.AsyncClient(timeout=10) as c:
                    lst = (await c.get(f"{url}/collections")).json()["result"]["collections"]
                    for entry in lst:
                        name = entry["name"]
                        detail = (await c.get(f"{url}/collections/{name}")).json().get("result", {})
                        cfg = detail.get("config", {}).get("params", {}).get("vectors", {})
                        if isinstance(cfg, dict) and "dense" in cfg:
                            dim = cfg["dense"].get("size")
                        elif isinstance(cfg, dict):
                            dim = cfg.get("size")
                        else:
                            dim = None
                        count = detail.get("points_count")
                        total += count or 0
                        # Judge configuration against REALITY: seed the dim
                        # cache with the actual dim we just read, so
                        # backend_for() resolves dim-driven for unmapped
                        # collections instead of falling to the default.
                        if isinstance(dim, int):
                            self._dim_cache.setdefault(name, dim)
                        if isinstance(cfg, dict) and "dense" in cfg:
                            self._named_vector_collections.add(name)
                        resolved_be, resolved_dim = self.backend_for(name)
                        entry_out: dict[str, Any] = {
                            "dim": dim,
                            "points": count,
                            "inferred_backend": _infer(dim),
                            "configured_backend": resolved_be.name,
                            "configured_dim": resolved_dim,
                        }
                        if isinstance(dim, int) and dim != resolved_dim:
                            entry_out["MISMATCH"] = True
                            out["mismatches"].append(
                                {
                                    "url": url,
                                    "collection": name,
                                    "actual_dim": dim,
                                    "configured_dim": resolved_dim,
                                    "hint": (
                                        "searches against this collection will fail; "
                                        "fix RAG_COLLECTION_BACKENDS or reindex"
                                    ),
                                }
                            )
                        cols[name] = entry_out
            except Exception as e:
                cols["_error"] = str(e)[:200]
            out["instances"][url] = cols
        out["total_points"] = total
        out["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return out

    async def federated_search(
        self,
        query: str,
        collections: list[str],
        limit_per: int = 8,
        min_similarity: float = 0.0,
        qdrant_url: str = "",
        rerank_tier: str = "fast",
    ) -> list[dict[str, Any]]:
        """Embed once per distinct backend, search each collection, RRF-merge."""
        from collections import defaultdict

        # Self-sufficient qdrant target: callers (HTTP route) may not pass a
        # URL — an empty one made every fan-out search die on a relative URL.
        if not qdrant_url:
            qdrant_url = self._qdrant_rest_urls()[0]

        # Recognize ANY vector space: resolve unmapped collections by their
        # ACTUAL dims before grouping, so a 2560 corpus never gets queried
        # with a 2048 nemotron vector (and vice versa) regardless of env.
        await self.resolve_collections(collections)

        # Group by (backend, TARGET DIM) — one backend can legitimately serve
        # multiple spaces (qwen8b native 2560 AND MRL-truncated 1024 legacy);
        # each distinct dim gets its own query vector.
        groups: dict[tuple[str, int], list[str]] = defaultdict(list)
        for coll in collections:
            be, dim = self.backend_for(coll)
            groups[(be.name, dim)].append(coll)

        async def _one_backend(
            bname: str, target_dim: int, colls: list[str]
        ) -> list[dict[str, Any]]:
            try:
                vec = await self.embed(query, bname, target_dim=target_dim)
            except Exception as e:
                logger.warning("fed_embed_failed backend=%s err=%s", bname, str(e)[:80])
                return []
            hits: list[dict[str, Any]] = []
            async with httpx.AsyncClient(timeout=20.0) as c:
                for coll in colls:
                    # Qdrant search API names vectors as {name, vector};
                    # the {"dense": [...]} shorthand is upsert-only.
                    vector_field: dict[str, Any] | list[float] = (
                        {"name": "dense", "vector": vec}
                        if coll in self._named_vector_collections
                        else vec
                    )
                    body: dict[str, Any] = {
                        "vector": vector_field,
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

        results = await asyncio.gather(
            *(_one_backend(bname, tdim, colls) for (bname, tdim), colls in groups.items())
        )

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

        # Tier-aware rerank of the merged cross-space results. Rerankers are
        # TEXT-based — space-agnostic — so one pass covers every backend.
        # Since 2026-09-14 both 'fast' and 'deep' serve the 8B family
        # (hosted live lane / local :8003); 'off' skips. Fail-open: RRF
        # order survives any reranker outage.
        if rerank_tier in ("fast", "deep") and merged:
            try:
                from rugbrain.reranker import rerank_tiered

                docs = [str(m.get("content", ""))[:1500] for m in merged]
                scored = await rerank_tiered(
                    query, docs, top_k=len(merged), tier=rerank_tier, raw=True
                )
                if scored:
                    reranked: list[dict[str, Any]] = []
                    for r in scored:
                        item = dict(merged[r["index"]])
                        item["rerank_score"] = round(r["relevance_score"], 6)
                        item["rerank_tier"] = rerank_tier
                        reranked.append(item)
                    return reranked
            except Exception as e:
                logger.warning("fed_rerank_failed tier=%s err=%s", rerank_tier, str(e)[:80])
        return merged


_singleton: MultiEmbedder | None = None


def get_multi_embedder() -> MultiEmbedder:
    global _singleton
    if _singleton is None:
        _singleton = MultiEmbedder()
    return _singleton
