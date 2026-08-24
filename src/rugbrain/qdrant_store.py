"""Qdrant vector store — lightweight wrapper over Qdrant REST API.

Replaces app.supabase_vector.SupabaseVectorStore with the same
search / hybrid_search / insert interface backed by Qdrant.

Connection strategy:
  1. Use QDRANT_URL env var (default: http://rmi-qdrant-v2:6333 for Docker)
  2. Fallback to http://127.0.0.1:{QDRANT_PORT or 6333} for local dev
  3. Fallback to http://localhost:{QDRANT_PORT or 6333}
  First URL that responds to GET / is used for the session.

Write gate: RMI_RAG_QDRANT_WRITE_MODE env var (default: readonly).
  - readonly: rugbrain writes only to its own collections (rag_* + known owned).
  - owner:    full write access (legacy behaviour).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Connection resolution ──────────────────────────────────────────────

QDRANT_URL = os.getenv("QDRANT_URL", "http://rmi-qdrant-v2:6333")
QDRANT_FALLBACK_URLS: list[str] = []

# Build fallback chain: primary → loopback variants
_primary = QDRANT_URL.rstrip("/")
_fallback_port = os.getenv("QDRANT_PORT", "6333")
QDRANT_FALLBACK_URLS = [
    _primary,
    f"http://127.0.0.1:{_fallback_port}",
    f"http://localhost:{_fallback_port}",
]
# Deduplicate while preserving order
_seen: set[str] = set()
QDRANT_FALLBACK_URLS = [
    u
    for u in QDRANT_FALLBACK_URLS
    if u not in _seen and not _seen.add(u)  # type: ignore[func-returns-value]
]

_resolved_url: str | None = None
_resolved_lock = asyncio.Lock()


async def _resolve_qdrant_url() -> str:
    """Probe fallback URLs and return the first responsive one.

    Caches the resolved URL so we only probe once per process lifetime.
    """
    global _resolved_url
    if _resolved_url is not None:
        return _resolved_url

    async with _resolved_lock:
        cached = _resolved_url
        if cached is not None:
            return cached  # type: ignore[unreachable]  # double-checked locking across coroutines

        for url in QDRANT_FALLBACK_URLS:
            try:
                async with httpx.AsyncClient(base_url=url, timeout=3.0) as c:
                    r = await c.get("/")
                    if r.status_code == 200:
                        _resolved_url = url
                        logger.info("qdrant_resolved url=%s", url)
                        return url
            except Exception:
                logger.debug("qdrant_probe_failed url=%s", url)
                continue

        # None worked — use primary anyway; callers will handle errors
        _resolved_url = _primary
        logger.warning("qdrant_unreachable using_fallback=%s", _resolved_url)
        return _resolved_url


async def _probe_qdrant() -> bool:
    """Simple boolean health probe — used by /health endpoint."""
    try:
        url = await _resolve_qdrant_url()
        async with httpx.AsyncClient(base_url=url, timeout=3.0) as c:
            r = await c.get("/")
            return r.status_code == 200
    except Exception:
        return False


# ── BM25 sparse embeddings (fastembed) ──────────────────────────────

# Name of the sparse vector in hybrid collections. Qdrant collection
# configs are immutable, so sparse support is provisioned by creating
# ``{name}_v2`` collections (see scripts/migrate_to_sparse.py) and
# pointing the original name at them via an alias.
SPARSE_VECTOR_NAME = "bm25"
# Name of the dense vector in hybrid collections (must match the config
# used by scripts/migrate_to_sparse.py when creating ``{name}_v2``).
DENSE_VECTOR_NAME = "dense"

_bm25_embedder: Any = None
_bm25_embedder_lock = threading.Lock()


def _get_bm25_embedder() -> Any | None:
    """Lazily load the fastembed ``Qdrant/bm25`` sparse embedder.

    Returns None when fastembed is unavailable or the model fails to
    load so callers can degrade to dense-only behaviour instead of
    crashing the ingest/search path.
    """
    global _bm25_embedder
    if _bm25_embedder is not None:
        return _bm25_embedder if _bm25_embedder is not False else None
    with _bm25_embedder_lock:
        try:
            from fastembed import SparseTextEmbedding

            _bm25_embedder = SparseTextEmbedding(model_name="Qdrant/bm25")
            logger.info("bm25_embedder_loaded model=Qdrant/bm25")
        except Exception as e:
            logger.warning("bm25_embedder_unavailable err=%s", e)
            _bm25_embedder = False
    return _bm25_embedder if _bm25_embedder is not False else None


async def _embed_sparse(text: str) -> tuple[list[int], list[float]] | None:
    """Compute a BM25 sparse vector ``(indices, values)`` for *text*.

    Returns None when *text* is empty or the BM25 embedder is not
    available (callers fall back to dense-only vectors).
    """
    if not text or not text.strip():
        return None
    embedder = _get_bm25_embedder()
    if embedder is None:
        return None
    try:
        sparse = next(iter(embedder.embed([text])))
        return (sparse.indices.tolist(), [float(v) for v in sparse.values.tolist()])
    except Exception as e:
        logger.warning("bm25_embed_error: %s", e)
        return None


# ── Payload filter helpers ─────────────────────────────────────────────

# Canonical filterable payload fields (see rugbrain.crypto_schema). Keeping
# the vocabulary here lets /api/v1/search validate user input before it hits
# Qdrant, and keeps crypto_schema free of Qdrant-specific concerns.
FILTERABLE_MATCH_FIELDS = (
    "severity",
    "chain",
    "attack_vector",
    "category",
    "protocol",
    "source_type",
    "collection",
    "scam_type",
)
# Range filters: payload field → Qdrant range keys allowed.
FILTERABLE_RANGE_FIELDS = (
    ("date_ts", ("gte", "lte")),
    ("amount_lost_usd", ("gte", "lte")),
)


def build_qdrant_filter(
    match: dict[str, str] | None = None,
    ranges: list[tuple[str, dict[str, float]]] | None = None,
) -> dict[str, Any] | None:
    """Build a Qdrant payload filter from exact-match and range constraints.

    ``match`` maps payload field → exact value (keyword match). ``ranges`` is
    a list of ``(field, {"gte": ..., "lte": ...})`` pairs. Returns ``None``
    when there is nothing to filter on (callers then omit the filter entirely).
    """
    must: list[dict[str, Any]] = []
    for key, value in (match or {}).items():
        if key and value is not None and str(value).strip():
            must.append({"key": key, "match": {"value": str(value)}})
    for key, constraint in ranges or []:
        if not constraint:
            continue
        if key and any(constraint.get(op) is not None for op in ("gte", "lte")):
            range_filter: dict[str, Any] = {}
            if constraint.get("gte") is not None:
                range_filter["gte"] = constraint["gte"]
            if constraint.get("lte") is not None:
                range_filter["lte"] = constraint["lte"]
            must.append({"key": key, "range": range_filter})
    if not must:
        return None
    return {"must": must}


def build_search_filters(
    severity: str | None = None,
    chain: str | None = None,
    attack_vector: str | None = None,
    category: str | None = None,
    protocol: str | None = None,
    source_type: str | None = None,
    collection: str | None = None,
    date_after: str | None = None,
    date_before: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
) -> dict[str, Any] | None:
    """Build a structured filter dict for ``hybrid_search`` from API params.

    Mirrors ``build_qdrant_filter`` output shape (``{"match": ..., "range": ...}``)
    but tolerates unparsed date strings and unknown fields, so endpoints can
    pass user input straight through. Date strings are converted to unix
    timestamps for the ``date_ts`` range field.
    """
    from rugbrain.crypto_schema import date_to_ts, normalize_severity

    match: dict[str, str] = {}
    if severity:
        match["severity"] = normalize_severity(severity)
    if chain:
        match["chain"] = str(chain).strip().lower()
    if attack_vector:
        match["attack_vector"] = str(attack_vector).strip().lower()
    if category:
        match["category"] = str(category).strip()
    if protocol:
        match["protocol"] = str(protocol).strip()
    if source_type:
        match["source_type"] = str(source_type).strip().lower()
    if collection:
        match["collection"] = str(collection).strip()

    ranges: list[tuple[str, dict[str, float]]] = []
    if date_after:
        ts = date_to_ts(date_after)
        if ts is not None:
            ranges.append(("date_ts", {"gte": ts}))
    if date_before:
        ts = date_to_ts(date_before)
        if ts is not None:
            ranges.append(("date_ts", {"lte": ts}))
    if amount_min is not None:
        ranges.append(("amount_lost_usd", {"gte": float(amount_min)}))
    if amount_max is not None:
        ranges.append(("amount_lost_usd", {"lte": float(amount_max)}))

    if not match and not ranges:
        return None
    return {"match": match, "range": ranges}


def apply_payload_filter(payload: dict[str, Any], qdrant_filter: dict[str, Any] | None) -> None:
    """Inline-add ``filter`` to a Qdrant request payload when given."""
    if qdrant_filter:
        payload["filter"] = qdrant_filter


def normalize_filters(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert a caller filter dict to a ready-to-send Qdrant filter.

    Accepts three shapes and returns None when there is nothing to filter on:
      - an already-built Qdrant filter (has a ``must`` key) → passed through
      - a structured ``{"match": {...}, "range": [...]}`` dict
      - a plain ``{field: value}`` exact-match dict (legacy form)
    """
    if not filters:
        return None
    if "must" in filters:
        return filters
    if "match" in filters or "range" in filters:
        return build_qdrant_filter(filters.get("match"), filters.get("range"))
    return build_qdrant_filter(filters, [])


# ── Collection management ─────────────────────────────────────────────

# Collections owned by rugbrain (write-allowed when mode=readonly).
# rmi-backend owns rmi_documents — rugbrain must never write there.
#
# NOTE (2026-08-16): the migration to hybrid `*_v2` collections
# (scripts/migrate_to_sparse.py) renamed the RAG collections
# (threat_intel → threat_intel_v2, hacks → hacks_v2, …) and NO Qdrant
# aliases exist, so the old names must NOT be used — writing to them
# auto-creates an empty fragment. The list below reflects the real
# collections the RAG ingest pipelines write to (per
# docs/COLLECTION_REGISTRY.md). All writers use *_v2 names — no legacy
# collection is owned anymore.
_RMI_RAG_OWNED_COLLECTIONS: frozenset[str] = frozenset(
    {
        # Hybrid-migrated RAG collections (real names — no aliases exist).
        "threat_intel_v2",
        "defi_exploits",
        "known_scams",
        "scam_intel_v2",
        "crime_reports",
        "contract_audits",
        "crypto_vulnerabilities",
        "hacks_v2",
        "news_v2",
        "research_v2",
        "wallet_v2",
        "scanner_v2",
        "security_feeds",
        "security_alerts",
        "regulation_enforcement",
        # Legacy collections still written by continuous ingest jobs.
        "news_articles",
        "education",
        "coding_knowledge",
        "contract_security",
        "solidity_patterns",
        "solana_patterns",
        "stablecoin_market",
        "security_patterns",
        "cryptography",
        "github_audits",
        "live_threats",
        # RAG-owned auxiliary / graph collections.
        "rag_threats",
        "rag_docs",
        "wallet_clusters",
        "bundle_patterns",
        "cluster_labels",
        "wallet_profiles",
        # Media/content corpus written by rugbrain's own ingest sources
        # (youtube.py, newsletters.py, podcasts.py). These were missing from
        # the registry, so the readonly write gate silently blocked their
        # ingesters (found 2026-08-24 while wiring the interim namespace).
        "youtube_videos",
        "newsletters",
        "podcast_episodes",
    }
)

_RMI_RAG_QDRANT_WRITE_MODE = os.getenv("RMI_RAG_QDRANT_WRITE_MODE", "readonly").lower()

# ── Interim collection namespace (2026-08-24 migration window) ─────────────
# During the v1→v2 re-embedding migration the active embedder may produce a
# different dimension than the canonical collections (free NVIDIA nemotron
# 2048-dim vs qwen3-embedding-4b 2560-dim). Vectors from different models
# must NEVER mix (see _resize / TOP-100 #25), so while
# RAG_INTERIM_COLLECTION_SUFFIX is set, ALL reads and writes route to a
# suffixed shadow namespace auto-created at the live embedder's native dim.
# Remove the env var once rag-embed-migration.service completes, then restart.
_INTERIM_SUFFIX = os.getenv("RAG_INTERIM_COLLECTION_SUFFIX", "")


def _map_collection(collection: str | None) -> str | None:
    """Route to the interim shadow namespace when one is configured."""
    if collection and _INTERIM_SUFFIX and not collection.endswith(_INTERIM_SUFFIX):
        return f"{collection}{_INTERIM_SUFFIX}"
    return collection


_existing_collections: set[str] = set()


async def _ensure_collection(collection: str, vector_size: int, client: httpx.AsyncClient) -> bool:
    if collection in _existing_collections:
        return True
    try:
        r = await client.get(f"/collections/{collection}")
        if r.status_code == 200:
            _existing_collections.add(collection)
            return True
    except Exception as e:
        logger.debug("qdrant_collection_probe_failed collection=%s err=%s", collection, e)
    try:
        payload: dict[str, Any] = {"vectors": {"size": vector_size, "distance": "Cosine"}}
        r = await client.put(f"/collections/{collection}", json=payload)
        ok = r.status_code in (200, 201)
        if ok:
            _existing_collections.add(collection)
        return ok
    except Exception as e:
        logger.warning("qdrant_create_collection_failed collection=%s err=%s", collection, e)
        return False


class QdrantVectorStore:
    """Lightweight Qdrant wrapper matching the SupabaseVectorStore interface.

    Methods exposed:
      - search(query_embedding, collection, limit, min_similarity)
      - hybrid_search(query_text, query_embedding, collection, limit) —
        dense + BM25 sparse → native Qdrant RRF fusion when the collection
        carries a ``bm25`` sparse vector, else dense + keyword re-rank
      - insert(doc_id, collection, embedding, content, metadata, source, severity)
    """

    def __init__(self, url: str | None = None):
        self._url = (url or QDRANT_URL).rstrip("/")
        # An explicitly provided URL wins over the env-based fallback
        # resolution (which probes QDRANT_FALLBACK_URLS). Without this, the
        # constructor argument was silently ignored by _get_client().
        self._explicit_url = url is not None
        self._client: httpx.AsyncClient | None = None
        # Per-instance cache of whether a collection carries a sparse
        # vector config (avoids a GET per insert/search in production).
        self._sparse_cache: dict[str, bool] = {}
        # Per-instance cache of whether a collection is configured with
        # named vectors (named dense and/or sparse). Named-vector
        # collections reject the bare vector format.
        self._named_cache: dict[str, bool] = {}

    @property
    def url(self) -> str:
        return self._url

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            resolved = self._url if self._explicit_url else await _resolve_qdrant_url()
            logger.debug("qdrant_client_init url=%s", resolved)
            self._client = httpx.AsyncClient(base_url=resolved, timeout=30.0)
        return self._client

    async def _collection_supports_sparse(self, collection: str, client: httpx.AsyncClient) -> bool:
        """Return True when *collection* carries a sparse-vector config.

        Results are cached per store instance so callers pay one extra
        GET per collection per process.
        """
        cached = self._sparse_cache.get(collection)
        if cached is not None:
            return cached
        supports = False
        try:
            r = await client.get(f"/collections/{collection}")
            if r.status_code == 200:
                params = r.json().get("result", {}).get("config", {}).get("params", {}) or {}
                supports = bool(params.get("sparse_vectors"))
        except Exception as e:
            logger.debug("qdrant_sparse_probe_failed collection=%s err=%s", collection, e)
        self._sparse_cache[collection] = supports
        return supports

    async def _collection_uses_named_vectors(
        self, collection: str, client: httpx.AsyncClient
    ) -> bool:
        """Return True when *collection* is configured with named vectors.

        A collection created with a named-vector config (``{"dense": ...}``
        or ``{"dense": ..., "bm25": ...}``) rejects a bare vector with
        "Not existing vector name", so the caller must send the named
        dense format. Only legacy collections created with a bare
        ``{"size": N, "distance": "Cosine"}`` config accept the bare
        vector. Named dense-only collections (e.g. ``news_v2``) do NOT
        report sparse support, so callers must probe named vectors, not
        just ``sparse_vectors``, before choosing the request shape.

        Results are cached per store instance so callers pay one extra
        GET per collection per process.
        """
        cached = self._named_cache.get(collection)
        if cached is not None:
            return cached
        named = False
        try:
            r = await client.get(f"/collections/{collection}")
            if r.status_code == 200:
                params = r.json().get("result", {}).get("config", {}).get("params", {}) or {}
                vectors = params.get("vectors")
                # Named dense vector config is a dict mapping name → spec.
                # A legacy config is a plain ``{"size": N, "distance": ...}``
                # which also happens to be a dict — distinguish by checking
                # for the named dense key we actually write.
                if isinstance(vectors, dict) and DENSE_VECTOR_NAME in vectors:
                    named = True
                if params.get("sparse_vectors"):
                    named = True
        except Exception as e:
            logger.debug("qdrant_named_probe_failed collection=%s err=%s", collection, e)
        self._named_cache[collection] = named
        return named

    async def search(
        self,
        query_embedding: list[float],
        collection: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.6,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search with optional payload filters.

        ``filters`` may be either:
          - a plain ``{field: value}`` dict (legacy exact-match form), or
          - a structured ``{"match": {...}, "range": [...]}`` dict as produced
            by ``build_search_filters``.
        """
        collection = _map_collection(collection)
        coll = collection or "default"
        client = await self._get_client()
        await _ensure_collection(coll, len(query_embedding), client)

        # Hybrid collections (created by scripts/migrate_to_sparse.py) and
        # named dense-only collections (e.g. news_v2) carry named vectors.
        # A bare vector is rejected by Qdrant against a named-vector
        # collection ("Not existing vector name"), so send the named dense
        # vector when the collection uses named vectors. Dense-only legacy
        # collections keep using the bare vector.
        vector_field: Any = query_embedding
        if await self._collection_uses_named_vectors(coll, client):
            vector_field = {"name": DENSE_VECTOR_NAME, "vector": query_embedding}

        payload: dict[str, Any] = {
            "vector": vector_field,
            "limit": limit,
            "score_threshold": min_similarity,
            "with_payload": True,
        }
        if filters:
            qdrant_filter = normalize_filters(filters)
            apply_payload_filter(payload, qdrant_filter)

        try:
            r = await client.post(f"/collections/{coll}/points/search", json=payload)
            if r.status_code != 200:
                logger.warning("qdrant_search_failed status=%d body=%s", r.status_code, r.text)
                return []
            data = r.json()
            results: list[dict[str, Any]] = []
            for point in data.get("result", []):
                payload_data = point.get("payload", {})
                results.append(
                    {
                        "id": point.get("id"),
                        "similarity": round(point.get("score", 0), 4),
                        "content": payload_data.get("content", ""),
                        "metadata": payload_data.get("metadata", {}),
                        "source": payload_data.get("source", ""),
                        "severity": payload_data.get("severity", ""),
                    }
                )
            return results
        except Exception as e:
            logger.warning("qdrant_search_error: %s", e)
            return []

    async def hybrid_search(
        self,
        query_text: str,
        query_embedding: list[float],
        collection: str | None = None,
        limit: int = 10,
        vector_weight: float = 0.7,
        min_similarity: float = 0.6,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Dense + BM25 sparse hybrid search with Reciprocal Rank Fusion.

        When the target collection carries the ``bm25`` sparse vector
        (post-migration) and the BM25 embedder is available, Qdrant fuses
        the dense and sparse result lists natively (``fusion="rrf"``).
        Otherwise it falls back to the dense-only search with lightweight
        keyword re-ranking so pre-migration collections keep working.
        collection = _map_collection(collection)

        ``filters`` uses the same structured shape as ``search``
        (``{"match": {...}, "range": [...]}``) and is applied to both the RRF
        request and the dense fallback.
        """
        coll = collection or "default"
        client = await self._get_client()
        await _ensure_collection(coll, len(query_embedding), client)

        qdrant_filter = normalize_filters(filters)

        if query_text and await self._collection_supports_sparse(coll, client):
            sparse = await _embed_sparse(query_text)
            if sparse is not None:
                fused = await self._hybrid_search_rrf(
                    client,
                    coll,
                    query_embedding,
                    sparse,
                    limit,
                    qdrant_filter=qdrant_filter,
                )
                if fused is not None:
                    return fused

        # ── Fallback: dense search + keyword re-rank (pre-sparse behaviour) ──
        results = await self.search(
            query_embedding,
            collection=coll,
            limit=limit * 2,
            min_similarity=min_similarity,
            filters=qdrant_filter,
        )

        if query_text and results:
            terms = set(query_text.lower().split())
            for r in results:
                content_lower = r.get("content", "").lower()
                kw_hits = sum(1 for t in terms if t in content_lower)
                keyword_score = kw_hits / max(1, len(terms))
                r["rrf_score"] = (
                    vector_weight * r["similarity"] + (1 - vector_weight) * keyword_score
                )
                r["match_type"] = "hybrid" if kw_hits > 0 else "vector"
            results.sort(
                key=lambda x: x.get("rrf_score", 0),
                reverse=True,
            )
        else:
            for r in results:
                r["rrf_score"] = r["similarity"]
                r["match_type"] = "vector"

        final: list[dict[str, Any]] = []
        for r in results[:limit]:
            r.pop("rrf_score", None)
            r.pop("match_type", None)
            final.append(r)
        return final

    async def _hybrid_search_rrf(
        self,
        client: httpx.AsyncClient,
        collection: str,
        dense_vector: list[float],
        sparse: tuple[list[int], list[float]],
        limit: int,
        qdrant_filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Native Qdrant hybrid search: named dense + BM25 sparse, RRF fusion.

        Returns formatted results, or None when Qdrant rejects the request
        so the caller can fall back to dense-only search.
        """
        payload: dict[str, Any] = {
            "vector": {"name": DENSE_VECTOR_NAME, "vector": dense_vector},
            "sparse_vector": {
                "name": SPARSE_VECTOR_NAME,
                "vector": {"indices": sparse[0], "values": sparse[1]},
            },
            "fusion": "rrf",
            "limit": limit,
            "with_payload": True,
        }
        apply_payload_filter(payload, qdrant_filter)
        try:
            r = await client.post(f"/collections/{collection}/points/search", json=payload)
            if r.status_code != 200:
                logger.warning(
                    "qdrant_hybrid_rrf_failed status=%d body=%s",
                    r.status_code,
                    r.text,
                )
                return None
            data = r.json()
            results: list[dict[str, Any]] = []
            for point in data.get("result", []):
                payload_data = point.get("payload", {})
                results.append(
                    {
                        "id": point.get("id"),
                        "similarity": round(point.get("score", 0), 4),
                        "content": payload_data.get("content", ""),
                        "metadata": payload_data.get("metadata", {}),
                        "source": payload_data.get("source", ""),
                        "severity": payload_data.get("severity", ""),
                    }
                )
            return results
        except Exception as e:
            logger.warning("qdrant_hybrid_rrf_error: %s", e)
            return None

    async def scroll(
        self,
        collection: str | None,
        limit: int = 100,
        offset: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Scroll through points in *collection* returning ``(results, next_offset)``.

        Used by ingestion-side consumers (scam pattern extraction) to read
        back every point in a collection without a vector query. Results are
        formatted like ``search`` (id + payload fields) with ``similarity``
        omitted (scroll has no score). Returns the next page offset when more
        points remain, else ``None``.
        """
        collection = _map_collection(collection)
        coll = collection or "default"
        client = await self._get_client()
        # NOTE: no _ensure_collection here — scroll is a READ and must never
        # create collections. The old size-1 placeholder creation poisoned
        # namespaces (a later real insert then hit "expected dim: 1").
        # A missing collection returns 404 below → empty page, which is the
        # correct answer for "nothing ingested yet".

        payload: dict[str, Any] = {
            "limit": limit,
            "with_payload": True,
        }
        if offset:
            payload["offset"] = offset
        if filters:
            qdrant_filter = normalize_filters(filters)
            apply_payload_filter(payload, qdrant_filter)

        try:
            r = await client.post(f"/collections/{coll}/points/scroll", json=payload)
            if r.status_code != 200:
                logger.warning("qdrant_scroll_failed status=%d body=%s", r.status_code, r.text)
                return [], None
            data = r.json()
            results: list[dict[str, Any]] = []
            for point in data.get("result", {}).get("points", []):
                payload_data = point.get("payload", {})
                results.append(
                    {
                        "id": point.get("id"),
                        "content": payload_data.get("content", ""),
                        "metadata": payload_data.get("metadata", {}),
                        "source": payload_data.get("source", ""),
                        "severity": payload_data.get("severity", ""),
                    }
                )
            next_offset = data.get("result", {}).get("next_page_offset")
            return results, next_offset
        except Exception as e:
            logger.warning("qdrant_scroll_error: %s", e)
            return [], None

    @staticmethod
    def _to_uuid(doc_id: str) -> str:
        """Convert a string doc_id to a deterministic UUID v5.

        Qdrant REST API requires point IDs to be unsigned integers or UUIDs.
        We use UUID v5 (SHA-1 namespace + name) for deterministic conversion.
        Namespace: 6ba7b810-9dad-11d1-80b4-00c04fd430c8 (DNS namespace)
        """
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))

    async def has_point(self, doc_id: str, collection: str | None) -> bool:
        """Return True when a deterministic point for *doc_id* already exists.

        Used by the shared ingest path (``ingest_common.store_documents``) to
        skip re-embedding content that is already stored — every pipeline that
        feeds the embedder goes through this check before calling Qwen3-4B, so
        re-running a crawl/cron does NOT re-embed the same chunks. Fail-open:
        any error (Qdrant down, malformed id) returns False so the caller
        proceeds to embed rather than dropping fresh content.
        """
        try:
            client = await self._get_client()
            collection = _map_collection(collection)
            point_id = self._to_uuid(doc_id)
            r = await client.get(f"/collections/{collection}/points/{point_id}")
            # 200 = exists; 404 = not found; anything else -> treat as exists-unknown (False)
            return r.status_code == 200
        except Exception as e:
            logger.debug("qdrant_has_point_error doc=%s err=%s", doc_id, str(e)[:120])
            return False

    async def insert(
        self,
        doc_id: str,
        collection: str | None,
        embedding: list[float],
        content: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "",
        severity: str = "medium",
        chain: str = "",
    ) -> bool:
        """Insert a document into *collection*.

        When the target collection is configured with named vectors
        (named dense, or dense + ``bm25`` sparse hybrid), the dense
        vector is stored under the ``dense`` name. For hybrid collections
        (created by scripts/migrate_to_sparse.py) a BM25 sparse vector is
        computed from *content* and stored alongside the dense vector so
        hybrid (RRF) search can use it. Legacy collections with a bare
        ``{"size": ...}`` config are written unchanged.
        """
        collection = _map_collection(collection) or "default"
        # Write gate: refuse writes to non-owned collections in readonly mode.
        # Ownership is evaluated on the CANONICAL name — the interim suffix is
        # a migration-window shim, not a different owner.
        gate_name = (
            collection[: -len(_INTERIM_SUFFIX)]
            if _INTERIM_SUFFIX and collection.endswith(_INTERIM_SUFFIX)
            else collection
        )
        if _RMI_RAG_QDRANT_WRITE_MODE == "readonly" and gate_name not in _RMI_RAG_OWNED_COLLECTIONS:
            logger.warning(
                "qdrant_write_gate_blocked collection=%s mode=readonly owned_by=rmi-backend",
                collection,
            )
            return False

        client = await self._get_client()
        await _ensure_collection(collection, len(embedding), client)

        point_id = self._to_uuid(doc_id)
        vector_field: Any = embedding
        if await self._collection_uses_named_vectors(collection, client):
            vector_field = {DENSE_VECTOR_NAME: embedding}
            if await self._collection_supports_sparse(collection, client):
                sparse = await _embed_sparse(content)
                if sparse is not None:
                    vector_field[SPARSE_VECTOR_NAME] = {
                        "indices": sparse[0],
                        "values": sparse[1],
                    }
        payload = {
            "points": [
                {
                    "id": point_id,
                    "vector": vector_field,
                    "payload": {
                        "content": content[:10000],
                        "metadata": metadata or {},
                        "source": source,
                        "severity": severity,
                        "chain": chain,
                        "collection": collection,
                    },
                }
            ]
        }
        try:
            r = await client.put(f"/collections/{collection}/points", json=payload)
            ok = r.status_code in (200, 201)
            if not ok:
                logger.warning(
                    "qdrant_insert_failed status=%d body=%s",
                    r.status_code,
                    r.text,
                )
            return ok
        except Exception as e:
            logger.warning("qdrant_insert_error: %s", e)
            return False

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


_store: QdrantVectorStore | None = None


async def get_qdrant_store() -> QdrantVectorStore:
    global _store
    if _store is None:
        _store = QdrantVectorStore()
    return _store
