"""RAG Embedder Protocol + Implementations.

Provides a clean embedder abstraction with:
  - EmbedderProtocol ABC for all embedders
  - InfinityEmbedder — OpenAI-compatible /embeddings client (PRIMARY;
    serves BAAI/bge-m3, 1024d)
  - BGE_M3_Embedder — HTTP client for the legacy bge-m3 Docker container
  - CachedEmbedder — in-memory LRU + optional Redis
  - FallbackEmbedder — primary → secondary with dimension normalization
  - get_embedder() — factory that wires everything from settings
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any, cast

import httpx
import numpy as np

from rugbrain._logging import get_logger
from rugbrain.observability.tracing import trace_span
from rugbrain.settings import settings

log = get_logger(__name__)


class EmbeddingError(RuntimeError):
    """Raised when an embedding backend fails to produce vectors.

    Subclasses ``RuntimeError`` so existing fail-open chains (e.g.
    ``FallbackEmbedder``) keep catching it unchanged.
    """


# ── Known embedding dimensions ─────────────────────────────────────────
KNOWN_DIMS: dict[str, int] = {
    "bge-m3": 1024,
    "llama-nemotron-embed-vl-1b-v2:free": 2048,
    "text-embedding-ada-002": 1536,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}


def lookup_dim(model_name: str) -> int:
    """Look up or default to 1024 for unknown models."""
    return KNOWN_DIMS.get(model_name, 1024)


def normalize_dim(vec: list[float], target_dim: int) -> list[float]:
    """Pad/truncate *vec* to *target_dim*, then L2-normalize.

    Lossy when truncating — only intended for cross-backend fallback
    where primary and secondary have different dimensions.
    """
    arr = np.asarray(vec, dtype=np.float32)
    if arr.shape[0] < target_dim:
        arr = np.concatenate([arr, np.zeros(target_dim - arr.shape[0], dtype=np.float32)])
    elif arr.shape[0] > target_dim:
        log.warning("embedder_dim_truncation from=%d to=%d", arr.shape[0], target_dim)
        arr = arr[:target_dim]
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm
    return cast(list[float], arr.tolist())


# ═══════════════════════════════════════════════════════════════════════════
# Protocol
# ═══════════════════════════════════════════════════════════════════════════


class EmbedderProtocol(ABC):
    """Abstract embedder. All implementations share this interface."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Output dimension of this embedder."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable identifier (e.g. 'bge-m3', 'nomic-embed-text')."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns one vector per input text.

        Raises RuntimeError if the backend is unreachable, returns wrong
        dimensions, or produces empty results.
        """

    async def embed_one(self, text: str) -> list[float]:
        """Convenience: embed a single text."""
        results = await self.embed([text])
        if not results:
            raise RuntimeError(f"{self.name} returned empty result for single text")
        return results[0]


# ═══════════════════════════════════════════════════════════════════════════
# Infinity  (OpenAI-compatible embeddings server — PRIMARY)
# ═══════════════════════════════════════════════════════════════════════════


class InfinityEmbedder(EmbedderProtocol):
    """Embedder backed by an Infinity server (OpenAI-compatible API).

    Calls ``POST {url}/embeddings`` with
    ``{"model": "BAAI/bge-m3", "input": [...]}`` and reads
    ``data[i].embedding`` — the same wire format as OpenAI, served locally
    by michaelfeil/infinity.

    PRIMARY RAG embedder; serves BAAI/bge-m3 via Infinity.
    """

    DEFAULT_MODEL: str = "BAAI/bge-m3"

    # Max batch size — keep per-request latency predictable and avoid
    # memory spikes on the Infinity server.
    MAX_BATCH_SIZE: int = 64

    def __init__(
        self,
        model: str = "",
        url: str = "",
        dim: int = 1024,
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        self._model = model or settings.embedder_primary_model or self.DEFAULT_MODEL
        self._url = (url or settings.infinity_url).rstrip("/")
        self._dim = dim
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    # ── client lifecycle ──────────────────────────────────────────────────
    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── EmbedderProtocol ──────────────────────────────────────────────────
    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return f"infinity:{self._model}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts (Phoenix-traced wrapper)."""
        if not texts:
            return []

        with trace_span(
            "rag.embedding.infinity",
            {
                "openinference.span.kind": "EMBEDDING",
                "openinference.embedding.model": self._model,
                "embedder.batch_size": len(texts),
            },
        ) as span:
            vectors = await self._embed_impl(texts)
            span.set_attribute("openinference.embedding.embeddings", len(vectors))
            span.set_attribute("embedder.result_count", len(vectors))
            return vectors

    async def _embed_impl(self, texts: list[str]) -> list[list[float]]:
        """Actual Infinity embed with batching + retries (unwrapped)."""
        # Split large batches into chunks to avoid gateway timeouts.
        if len(texts) > self.MAX_BATCH_SIZE:
            return await self._embed_chunked(texts)

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._http.post(
                    f"{self._url}/embeddings",
                    json={"model": self._model, "input": texts},
                )
                resp.raise_for_status()
                items = resp.json().get("data") or []
                if len(items) != len(texts):
                    raise RuntimeError(f"Infinity returned {len(items)} embeddings for {len(texts)} texts")
                vectors: list[list[float]] = [
                    item["embedding"] for item in sorted(items, key=lambda i: i.get("index", 0))
                ]
                self._validate(vectors)
                return vectors
            except (httpx.HTTPError, OSError, RuntimeError, json.JSONDecodeError, KeyError, TypeError) as exc:
                last_exc = exc
                log.warning("infinity_embed_attempt_failed attempt=%d error=%s", attempt, exc)
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))

        raise RuntimeError(f"Infinity embed failed after {self._max_retries + 1} attempts") from last_exc

    async def _embed_chunked(self, texts: list[str]) -> list[list[float]]:
        """Split large batches into chunks and embed concurrently."""
        chunks = [texts[i : i + self.MAX_BATCH_SIZE] for i in range(0, len(texts), self.MAX_BATCH_SIZE)]
        sem = asyncio.Semaphore(2)  # max 2 concurrent chunk requests

        async def _do(chunk: list[str]) -> list[list[float]]:
            async with sem:
                for attempt in range(self._max_retries + 1):
                    try:
                        resp = await self._http.post(
                            f"{self._url}/embeddings",
                            json={"model": self._model, "input": chunk},
                        )
                        resp.raise_for_status()
                        items = resp.json().get("data") or []
                        if len(items) != len(chunk):
                            raise RuntimeError(f"expected {len(chunk)} vectors, got {len(items)}")
                        vecs: list[list[float]] = [
                            item["embedding"] for item in sorted(items, key=lambda i: i.get("index", 0))
                        ]
                        self._validate(vecs)
                        return vecs
                    except (httpx.HTTPError, OSError, RuntimeError, json.JSONDecodeError, KeyError, TypeError):
                        if attempt < self._max_retries:
                            await asyncio.sleep(0.5 * (attempt + 1))
                        else:
                            raise

            raise RuntimeError(f"Infinity embed failed after {self._max_retries + 1} attempts")

        chunk_results = await asyncio.gather(*[_do(c) for c in chunks])

        # Flatten
        result: list[list[float]] = []
        for cr in chunk_results:
            result.extend(cr)
        return result

    def _validate(self, vectors: list[list[float]]) -> None:
        for i, vec in enumerate(vectors):
            actual = len(vec)
            if actual != self._dim:
                # First mismatch: auto-detect and update dimension so the
                # caller's collections stay consistent with the server.
                if actual > 0:
                    log.info(
                        "infinity_auto_dim model=%s expected=%d actual=%d",
                        self._model,
                        self._dim,
                        actual,
                    )
                    self._dim = actual
                    return
                raise RuntimeError(f"Infinity returned {actual}d vector for text[{i}], expected {self._dim}d")


# ═══════════════════════════════════════════════════════════════════════════
# BGE-M3  (Docker container HTTP API)
# ═══════════════════════════════════════════════════════════════════════════


class BGE_M3_Embedder(EmbedderProtocol):
    """BGE-M3 embedder via its container's HTTP API.

    Expects a FastAPI service at *url* with a POST ``/embed`` endpoint
    that accepts ``{"texts": ["..."]}`` and returns
    ``{"embeddings": [[...]], "dim": 1024}``.
    """

    def __init__(
        self,
        url: str = "",
        dim: int = 1024,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._url = (url or settings.bge_m3_url).rstrip("/")
        self._dim = dim
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    # ── client lifecycle ──────────────────────────────────────────────────
    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── EmbedderProtocol ──────────────────────────────────────────────────
    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return "bge-m3"

    MAX_CHUNK_SIZE: int = 32

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # Split large batches into chunks to avoid gateway timeouts.
        if len(texts) > self.MAX_CHUNK_SIZE:
            return await self._embed_chunked(texts)

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._http.post(
                    f"{self._url}/embed",
                    json={"texts": texts},
                )
                resp.raise_for_status()
                data = resp.json()
                vectors: list[list[float]] | None = data.get("embeddings")
                if not vectors:
                    raise RuntimeError("bge-m3 returned empty embeddings list")
                self._validate(vectors)
                return vectors
            except (httpx.HTTPError, OSError, RuntimeError, json.JSONDecodeError) as exc:
                last_exc = exc
                log.warning("bge_m3_attempt_failed attempt=%d error=%s", attempt, exc)
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))

        raise RuntimeError(f"bge-m3 embed failed after {self._max_retries + 1} attempts") from last_exc

    async def _embed_chunked(self, texts: list[str]) -> list[list[float]]:
        """Split large batches into chunks and embed concurrently."""
        chunks = [texts[i : i + self.MAX_CHUNK_SIZE] for i in range(0, len(texts), self.MAX_CHUNK_SIZE)]
        sem = asyncio.Semaphore(2)  # max 2 concurrent chunk requests

        async def _do(chunk: list[str]) -> list[list[float]]:
            async with sem:
                for attempt in range(self._max_retries + 1):
                    try:
                        resp = await self._http.post(
                            f"{self._url}/embed",
                            json={"texts": chunk},
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        vecs: list[list[float]] | None = data.get("embeddings")
                        if not vecs:
                            raise RuntimeError("empty embeddings")
                        if len(vecs) != len(chunk):
                            raise RuntimeError(f"expected {len(chunk)} vectors, got {len(vecs)}")
                        self._validate(vecs)
                        return vecs
                    except (httpx.HTTPError, OSError, RuntimeError, json.JSONDecodeError):
                        if attempt < self._max_retries:
                            await asyncio.sleep(0.5 * (attempt + 1))
                        else:
                            raise

            raise RuntimeError(f"bge-m3 embed failed after {self._max_retries + 1} attempts")

        tasks = [_do(c) for c in chunks]
        chunk_results = await asyncio.gather(*tasks)

        # Flatten
        result: list[list[float]] = []
        for cr in chunk_results:
            result.extend(cr)
        return result

    def _validate(self, vectors: list[list[float]]) -> None:
        for i, vec in enumerate(vectors):
            actual = len(vec)
            if actual != self._dim:
                raise RuntimeError(f"bge-m3 returned {actual}d vector for text[{i}], expected {self._dim}d")


# ═══════════════════════════════════════════════════════════════════════════
# CachedEmbedder  (in-memory LRU + optional Redis)
# ═══════════════════════════════════════════════════════════════════════════


class _MemoryCache:
    """Simple TTL-based in-memory cache with LRU eviction."""

    def __init__(self, maxsize: int = 10_000, default_ttl: int = 86_400) -> None:
        self._maxsize = maxsize
        self._default_ttl = default_ttl
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            self._store.pop(key, None)
            self._misses += 1
            return None
        self._hits += 1
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        expires = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
        self._store[key] = (expires, value)
        self._store.move_to_end(key)
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)

    @property
    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total * 100, 1) if total > 0 else 0.0,
        }


class CachedEmbedder(EmbedderProtocol):
    """Wraps any ``EmbedderProtocol`` with an in-memory LRU cache.

    Optionally uses Redis as a second-level cache when available.
    Cache keys are ``embed:{dim}:{md5(text)}``.
    """

    def __init__(
        self,
        inner: EmbedderProtocol,
        ttl: int = 86_400,
        maxsize: int = 10_000,
        redis_url: str = "",
    ) -> None:
        self._inner = inner
        self._ttl = ttl
        self._mem = _MemoryCache(maxsize=maxsize, default_ttl=ttl)
        self._redis: Any = None
        self._redis_ok = False
        # Held references to fire-and-forget Redis writes (kept alive to
        # prevent GC cancelling in-flight tasks; see RUF006).
        self._bg_tasks: set[asyncio.Task[Any]] = set()
        if redis_url or os.getenv("REDIS_URL"):
            self._init_redis(redis_url or "")

    def _init_redis(self, url: str) -> None:
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                url or settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            self._redis_ok = True
        except Exception as exc:
            log.info("cached_embedder_redis_unavailable: %s", exc)
            self._redis_ok = False

    @property
    def dim(self) -> int:
        return self._inner.dim

    @property
    def name(self) -> str:
        return f"cached:{self._inner.name}"

    def _cache_key(self, text: str) -> str:
        h = hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()
        return f"embed:{self._inner.dim}:{h}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        with trace_span(
            "rag.embedding.cached",
            {
                "openinference.span.kind": "EMBEDDING",
                "openinference.embedding.model": self._inner.name,
                "embedder.batch_size": len(texts),
            },
        ) as span:
            # Check memory cache for all texts
            keys = [self._cache_key(t) for t in texts]
            cached = [self._mem.get(k) for k in keys]

            # Identify uncached indices
            uncached_indices = [i for i, v in enumerate(cached) if v is None]
            if not uncached_indices:
                span.set_attribute("embedder.cache_hit", True)
                span.set_attribute("openinference.embedding.embeddings", len(cached))
                return cached  # type: ignore[return-value]

            # Hit the inner embedder for uncached texts
            uncached_texts = [texts[i] for i in uncached_indices]
            try:
                fresh = await self._inner.embed(uncached_texts)
            except RuntimeError as exc:
                log.error(
                    "cached_embedder_inner_failed uncached=%d error=%s",
                    len(uncached_indices),
                    exc,
                )
                # Never return zero vectors: they silently corrupt ANN search
                # results AND poison the cache with garbage. Propagate the
                # error so callers can retry or fail loudly.
                raise EmbeddingError(f"Failed to embed batch of {len(uncached_texts)}: {exc}") from exc

            # Store in memory cache
            for idx, vec in zip(uncached_indices, fresh, strict=False):
                self._mem.set(keys[idx], vec, self._ttl)
                cached[idx] = vec

            # Fire-and-forget Redis write for uncached vectors
            if self._redis_ok:
                self._spawn_bg(self._redis_bulk_set(keys, fresh, uncached_indices))

            span.set_attribute("embedder.cache_hit", False)
            span.set_attribute("openinference.embedding.embeddings", len(cached))
            return cached  # type: ignore[return-value]

    async def _redis_bulk_set(
        self,
        keys: list[str],
        fresh: list[list[float]],
        indices: list[int],
    ) -> None:
        try:
            pipe = self._redis.pipeline()
            for idx, vec in zip(indices, fresh, strict=False):
                pipe.setex(keys[idx], self._ttl, json.dumps(vec))
            await pipe.execute()
        except Exception:
            log.debug("redis_bulk_set_failed")

    async def embed_one(self, text: str) -> list[float]:
        key = self._cache_key(text)
        cached = self._mem.get(key)
        if cached is not None:
            return cast(list[float], cached)

        vec = await self._inner.embed_one(text)
        self._mem.set(key, vec, self._ttl)
        if self._redis_ok:
            self._spawn_bg(self._redis_single_set(key, vec))
        return vec

    def _spawn_bg(self, coro: Any) -> None:
        """Track a background task so it isn't garbage-collected mid-flight."""
        task = asyncio.ensure_future(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _redis_single_set(self, key: str, vec: list[float]) -> None:
        try:
            await self._redis.setex(key, self._ttl, json.dumps(vec))
        except Exception:
            log.debug("redis_single_set_failed %s", key[:20])

    def stats(self) -> dict[str, Any]:
        return {"memory": self._mem.stats, "redis_ok": self._redis_ok}


# ═══════════════════════════════════════════════════════════════════════════
# FallbackEmbedder  (primary -> secondary with dimension normalization)
# ═══════════════════════════════════════════════════════════════════════════


class FallbackEmbedder(EmbedderProtocol):
    """Tries the *primary* embedder first; falls back to *secondary* on failure.

    If the secondary returns a different dimension, vectors are padded/truncated
    and L2-normalized to match the primary's dimension so downstream collections
    remain compatible.
    """

    def __init__(
        self,
        primary: EmbedderProtocol,
        secondary: EmbedderProtocol,
    ) -> None:
        self._primary = primary
        self._secondary = secondary

    @property
    def dim(self) -> int:
        return self._primary.dim

    @property
    def name(self) -> str:
        primary_name = self._primary.name
        secondary_name = self._secondary.name
        return f"{primary_name}->{secondary_name}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return await self._primary.embed(texts)
        except (RuntimeError, httpx.HTTPError, OSError, json.JSONDecodeError) as exc:
            log.warning(
                "fallback_embedder_primary_failed primary=%s error=%s",
                self._primary.name,
                exc,
            )

        # Fallback: secondary may have a different dimension
        try:
            vectors = await self._secondary.embed(texts)
        except (RuntimeError, httpx.HTTPError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"FallbackEmbedder: both primary ({self._primary.name}) and "
                f"secondary ({self._secondary.name}) failed. Last error: {exc}"
            ) from exc

        target_dim = self._primary.dim
        if vectors and len(vectors[0]) != target_dim:
            log.info(
                "fallback_embedder_normalizing from_dim=%d to_dim=%d",
                len(vectors[0]),
                target_dim,
            )
            vectors = [normalize_dim(v, target_dim) for v in vectors]

        return vectors


# ═══════════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════════


def _create_bge_m3() -> BGE_M3_Embedder:
    return BGE_M3_Embedder(url=settings.bge_m3_url, dim=1024)


def _create_infinity(model: str | None = None) -> InfinityEmbedder:
    return InfinityEmbedder(model=model or settings.embedder_primary_model)


def get_embedder(
    primary: str | None = None,
    enable_cache: bool = True,
) -> EmbedderProtocol:
    """Build and return a fully-wired embedder chain.

    Embedding is served exclusively by Infinity (OpenAI-compatible
    ``/embeddings``); the legacy bge-m3 container remains only as an
    explicit opt-in. No fallback chain is wired.

    Args:
        primary: Primary backend name (``"infinity"`` | ``"bge-m3"``).
                 Defaults to ``settings.embedder_primary``.
        enable_cache: Wrap in ``CachedEmbedder`` for in-memory + Redis cache.
    Returns:
        An ``EmbedderProtocol`` instance ready for ``await embed(texts)``.

    Examples::

        embedder = get_embedder()
        vectors = await embedder.embed(["scam token 0x...", "phishing site..."])

        embedder_no_cache = get_embedder(enable_cache=False)
    """
    primary_name = primary or settings.embedder_primary

    # Build primary
    if primary_name == "infinity":
        primary_embedder: EmbedderProtocol = _create_infinity()
    elif primary_name == "bge-m3":
        primary_embedder = _create_bge_m3()
    else:
        log.warning(
            "get_embedder_unknown_primary=%s falling_back_to_infinity",
            primary_name,
        )
        primary_embedder = _create_infinity()

    # Wrap in cache
    chain: EmbedderProtocol = primary_embedder
    if enable_cache:
        chain = CachedEmbedder(
            inner=primary_embedder,
            ttl=settings.embedder_cache_ttl,
            maxsize=settings.embedder_cache_maxsize,
        )

    return chain


# ═══════════════════════════════════════════════════════════════════════════
# Singleton for application-wide use
# ═══════════════════════════════════════════════════════════════════════════

_embedder: EmbedderProtocol | None = None


def get_default_embedder() -> EmbedderProtocol:
    """Lazily-initialized singleton embedder.

    Use this in application code rather than calling ``get_embedder()``
    repeatedly so that connections and caches are shared.
    """
    global _embedder
    if _embedder is None:
        _embedder = get_embedder()
    return _embedder


async def shutdown_embedder() -> None:
    """Close pooled connections. Call during app shutdown."""
    global _embedder
    if _embedder is None:
        return

    # Unwrap to find the inner HTTP clients
    inner = _embedder
    # Unwrap CachedEmbedder to reach the FallbackEmbedder / raw embedder
    if isinstance(inner, CachedEmbedder):
        inner = inner._inner

    if isinstance(inner, FallbackEmbedder):
        for sub in [inner._primary, inner._secondary]:
            if hasattr(sub, "close"):
                await sub.close()
    elif hasattr(inner, "close"):
        await inner.close()

    _embedder = None
