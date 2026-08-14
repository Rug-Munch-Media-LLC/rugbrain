"""M3 RAG Engine - three-pillar search + ingest.

The missing module that app/rag/service.py was importing (the legacy
app.rag_engine was nuked during consolidation but never replaced).

Three-Pillar Search (per 2026 RAG standards):
  Pillar 1: ANN vector similarity (semantic match)
  Pillar 2: BM25-lite keyword search (lexical match)
  Pillar 3: Metadata filter (structured constraints)
  Fusion: Reciprocal Rank Fusion (RRF) - k=60

Ingest:
  - Chunk → embed → store in ANN index + Redis doc store
  - SHA-256 dedup per collection
  - Per-chunk doc_id = "{collection}:{hash}:{idx}"
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx

from rugbrain._logging import get_logger
from rugbrain.core.chunking import (
    Chunk,
    chunk_text,
    content_hash,
    estimate_tokens,
    is_duplicate,
    mark_ingested,
)
from rugbrain.embed.embeddings import current_backend, get_embedding
from rugbrain.observability.tracing import trace_span
from rugbrain.settings import settings
from rugbrain.storage.ann_index import Hit, get_index

log = get_logger(__name__)

# RRF constant
_RRF_K = 60

# Hard context-token budget applied AFTER rerank on the primary search
# facade (env override wins, e.g. ``RAG_CONTEXT_MAX_TOKENS=2048``).
_CONTEXT_MAX_TOKENS: int = settings.context_max_tokens

# Max characters sent per candidate to the cross-encoder (bge-reranker-v2-m3
# truncates to 512 tokens internally; 2000 chars is a safe upper bound).
_RERANK_MAX_CHARS = 2000

# Qdrant fallback: collections that live in Qdrant (e.g. `news` seeded by
# rmi-rag/scripts/news_to_rag.py) are searchable even though the in-process
# numpy/Redis index has no data for them. Cache which collections exist.
# Resolved via app.config (env var wins; Docker name inside the network;
# host-mapped localhost on host-side systemd runs).
_QDRANT_URL = settings.qdrant_url.rstrip("/")
_QDRANT_COLLECTIONS: set[str] = set()
_QDRANT_PROBED = False

# Primary-store routing: RAG_STORE=qdrant (default) serves search through
# the unified Qdrant hybrid store (app.rag.qdrant_store); RAG_STORE=legacy
# keeps the numpy/Redis + BM25-lite pillar path. Qdrant failures fall back
# to the legacy path automatically.
_DEFAULT_STORE: str = settings.store.strip().lower()

# Collection name resolved for the primary Qdrant store when the requested
# RAG collection does not itself exist in Qdrant (env-driven, default
# "rag_documents" — mirrors app.rag.qdrant_store._resolve_collection).
_DEFAULT_QDRANT_COLLECTION: str = settings.qdrant_collection.strip() or "rag_documents"

# Resolved collection cache for the primary store path (requested RAG
# collection → Qdrant collection name).
_QDRANT_RESOLVED: dict[str, str] = {}

# Lexical stopwords for the query→sparse-vector builder (mirrors the set
# used by _keyword_search so the two lexical paths stay consistent).
_SEARCH_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "this",
        "that",
        "and",
        "for",
        "with",
        "from",
        "has",
        "have",
        "was",
        "were",
        "been",
        "are",
        "not",
        "but",
        "its",
        "all",
        "can",
        "will",
        "just",
        "very",
        "also",
        "than",
        "then",
        "each",
        "which",
        "what",
        "when",
        "where",
        "how",
        "who",
    }
)


async def _qdrant_collection_exists(client: httpx.AsyncClient, collection: str) -> bool:
    """Probe Qdrant once per process for a collection's existence.

    Resolves both real collections and aliases (e.g. ``news`` → ``news_v2``)
    so legacy collection names stay searchable. ``GET /collections`` does not
    return alias names in Qdrant 1.18, so aliases are probed separately via
    ``GET /aliases``; without this the Qdrant fallback returned 0 results for
    every alias-named collection (news, scams, scanner, research, ...).
    """
    global _QDRANT_PROBED
    if collection in _QDRANT_COLLECTIONS:
        return True
    if not _QDRANT_PROBED:
        try:
            r = await client.get("/collections")
            if r.status_code == 200:
                names = {c.get("name") for c in r.json().get("result", {}).get("collections", [])}
                _QDRANT_COLLECTIONS.update(names)
            a = await client.get("/aliases")
            if a.status_code == 200:
                alias_names = {c.get("alias_name") for c in a.json().get("result", {}).get("aliases", [])}
                _QDRANT_COLLECTIONS.update(alias_names)
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            log.debug("qdrant_probe_failed err=%s", e)
        _QDRANT_PROBED = True
    return collection in _QDRANT_COLLECTIONS


async def _qdrant_uses_named_vectors(client: httpx.AsyncClient, collection: str) -> bool:
    """Probe a collection's config to detect named-vector collections.

    v2 collections (e.g. ``threat_intel_v2``, ``scanner_v2``) are created
    with named vectors (``{"dense": ..., "bm25": ...}``) and reject a bare
    vector with "Not existing vector name". Dense-only legacy collections
    accept the bare vector format. Returns False on any probe failure
    (missing collection, unreachable Qdrant) so the caller falls back to
    the legacy request shape.
    """
    try:
        r = await client.get(f"/collections/{collection}")
        if r.status_code != 200:
            return False
        vectors = r.json().get("result", {}).get("config", {}).get("params", {}).get("vectors", {})
        return isinstance(vectors, dict) and "dense" in vectors
    except (httpx.HTTPError, httpx.TimeoutException, ValueError, KeyError) as e:
        log.debug("qdrant_config_probe_failed collection=%s err=%s", collection, e)
        return False


async def _qdrant_search(
    query_vec: list[float],
    collection: str,
    limit: int = 20,
    score_threshold: float = 0.0,
) -> list[Hit]:
    """Vector search against Qdrant (fallback for Qdrant-backed collections).

    Handles both legacy collections (bare vector) and v2 collections that
    use named vectors. The collection config is probed first so the named
    ``dense`` vector is sent when required — a bare vector against a
    named-vector collection is rejected by Qdrant with "Not existing
    vector name", which previously made v2 data unreachable (0 results).

    ``score_threshold`` defaults to 0.0 = no threshold, matching the
    legacy behavior (three_pillar_search passes ``min_similarity`` and
    only filters when it is > 0). v2 bge-m3 cosine scores for relevant
    matches cluster around 0.45-0.70, so a high fixed default would make
    the fallback return 0 results for typical queries.

    Returns Hit objects in the same shape as the local ANN index so the
    existing RRF fusion / response wrapping keeps working unchanged.
    Returns [] when the collection is missing or Qdrant is unreachable.
    """
    try:
        async with httpx.AsyncClient(base_url=_QDRANT_URL, timeout=10.0) as client:
            if not await _qdrant_collection_exists(client, collection):
                return []
            # v2 collections carry named vectors — send the named dense
            # vector instead of the bare vector format.
            vector_field: Any = query_vec
            if await _qdrant_uses_named_vectors(client, collection):
                vector_field = {"name": "dense", "vector": query_vec}
            payload: dict[str, Any] = {
                "vector": vector_field,
                "limit": limit,
                "score_threshold": score_threshold if score_threshold > 0 else None,
                "with_payload": True,
            }
            r = await client.post(f"/collections/{collection}/points/search", json=payload)
            if r.status_code != 200:
                log.warning("qdrant_search_failed status=%d body=%s", r.status_code, r.text[:200])
                return []
            hits: list[Hit] = []
            for point in r.json().get("result", []):
                p = point.get("payload", {})
                meta = p.get("metadata", {}) or {}
                hits.append(
                    Hit(
                        doc_id=str(point.get("id", "")),
                        score=float(point.get("score", 0.0) or 0.0),
                        text=p.get("content", ""),
                        metadata={**meta, "source": p.get("source", meta.get("source", ""))},
                    )
                )
            return hits
    except (httpx.HTTPError, httpx.TimeoutException, ValueError, KeyError) as e:
        log.debug("qdrant_search_error err=%s", e)
        return []


# ── Three-pillar search ──────────────────────────────────────────────
async def _resolve_qdrant_collection(collection: str) -> str:
    """Map the requested RAG collection to a Qdrant collection name.

    Uses the collection itself when it exists in Qdrant (probe cached once
    per process by ``_qdrant_collection_exists``), otherwise the default
    store collection (``RAG_QDRANT_COLLECTION`` / ``rag_documents``).
    """
    if collection in _QDRANT_RESOLVED:
        return _QDRANT_RESOLVED[collection]
    try:
        async with httpx.AsyncClient(base_url=_QDRANT_URL, timeout=5.0) as client:
            resolved = collection if await _qdrant_collection_exists(client, collection) else _DEFAULT_QDRANT_COLLECTION
    except (httpx.HTTPError, httpx.TimeoutException):
        resolved = _DEFAULT_QDRANT_COLLECTION
    _QDRANT_RESOLVED[collection] = resolved
    return resolved


def _build_sparse_query(query: str) -> dict[str, Any] | None:
    """Lexical sparse vector (``{"indices": [...], "values": [...]}``) from a query.

    Stand-in for a learned sparse encoder (SPLADE) until one is wired into the
    primary path: tokens are hashed into a 32-bit index space and weighted by
    a small TF boost so the sparse pillar can fuse with the dense pillar via
    Qdrant RRF. Returns None for stopword-only/empty queries (dense-only).
    """
    import hashlib
    import re

    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_#]*", query.lower())
    tokens = [t for t in tokens if t not in _SEARCH_STOPWORDS and len(t) > 1]
    if not tokens:
        return None
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    indices: list[int] = []
    values: list[float] = []
    for t, c in counts.items():
        indices.append(int(hashlib.sha256(t.encode("utf-8")).hexdigest()[:8], 16))
        values.append(1.0 + 0.5 * (c - 1))
    return {"indices": indices, "values": values}


async def _qdrant_primary_search(
    query: str,
    qvec: list[float],
    collection: str,
    top_k: int,
    min_similarity: float,
    filters: dict[str, Any] | None,
) -> tuple[list[Hit], str]:
    """Qdrant hybrid retrieval (dense + lexical-sparse, RRF) — the primary path.

    Returns ``(hits, backend)`` where ``backend`` is ``"legacy"`` when the
    caller should run the legacy pillar path instead (Qdrant unreachable,
    error, or returned nothing). Hits are engine ``Hit`` objects; metadata
    filters are applied post-retrieval to preserve legacy semantics (the
    legacy path filtered ``hit.metadata``, not raw payload fields).
    """
    from rugbrain.storage.qdrant_store import PREFETCH_MULTIPLIER, get_qdrant_store

    try:
        qdrant_collection = await _resolve_qdrant_collection(collection)
        store = get_qdrant_store(qdrant_collection)
        raw = await store.search(
            vector=qvec,
            sparse_vector=_build_sparse_query(query),
            filters=filters or None,
            limit=top_k * PREFETCH_MULTIPLIER,
        )
    except Exception as exc:
        log.warning("rag_qdrant_primary_error collection=%s err=%s", collection, exc)
        return [], "legacy"

    if not raw:
        log.info("rag_qdrant_primary_empty collection=%s falling_back=legacy", collection)
        return [], "legacy"

    hits: list[Hit] = []
    for item in raw:
        score = float(item.get("score", 0.0) or 0.0)
        if min_similarity > 0 and score < min_similarity:
            continue
        hits.append(
            Hit(
                doc_id=str(item.get("doc_id", "")),
                score=score,
                text=str(item.get("text", "")),
                metadata=item.get("metadata", {}) or {},
            )
        )

    # Post-filter to match legacy exact-match semantics on hit.metadata.
    filtered = _apply_filters(hits, filters or {})
    return filtered[:top_k], "qdrant"


async def _legacy_pillar_search(
    query: str,
    qvec: list[float],
    collection: str,
    top_k: int,
    min_similarity: float,
    filters: dict[str, Any] | None,
) -> list[Hit]:
    """The legacy three-pillar path: numpy ANN + BM25-lite + RRF + Qdrant fallback."""
    # Pillar 1: ANN vector search
    idx = get_index(collection)
    ann_hits = idx.search(qvec, top_k=top_k * 3, min_similarity=min_similarity)

    # Pillar 2: keyword (BM25-lite via Redis text scan on stored docs)
    keyword_hits = await _keyword_search(query, collection, top_k * 3)

    # Pillar 3: metadata filter (apply to ann+keyword results)
    _apply_filters(ann_hits + keyword_hits, filters or {})

    # RRF fusion
    fused = _reciprocal_rank_fusion([ann_hits, keyword_hits], top_k=top_k)

    # Apply filters post-fusion
    if filters:
        fused = [h for h in fused if _matches_filters(h, filters)]

    # Qdrant fallback: if the in-process index has no data for this
    # collection, consult Qdrant (e.g. `news` seeded by news_to_rag.py).
    # Strictly additive — collections with local data are untouched.
    if not fused:
        qdrant_hits = await _qdrant_search(qvec, collection, limit=top_k, score_threshold=min_similarity)
        if qdrant_hits:
            fused = qdrant_hits[:top_k]
            log.info(
                "rag_search_qdrant_fallback collection=%s hits=%d",
                collection,
                len(fused),
            )
    return fused


async def _rerank_hits(query: str, hits: list[Hit]) -> list[Hit]:
    """Cross-encoder rerank (Infinity, BAAI/bge-reranker-v2-m3) — MANDATORY.

    Reranks *hits* against *query* and reorders them by cross-encoder
    relevance, updating each hit's ``score`` and
    ``metadata["rerank_score"]``. This is the mandatory post-retrieval
    step on the primary search facade; there is no skip path. Fail-open:
    if the reranker is unreachable or returns nothing, the pre-rerank
    order is kept (search must never break because reranking is down).
    """
    if len(hits) < 2:
        return hits
    try:
        from rugbrain.core.rerank import rerank_results

        docs = [(h.text or "")[:_RERANK_MAX_CHARS] for h in hits]
        reranked = await rerank_results(query, docs, top_n=len(hits))
        if not reranked:
            return hits
        ordered: list[Hit] = []
        for item in sorted(reranked, key=lambda x: -float(x.get("relevance_score", 0.0))):
            idx = int(item.get("index", -1))
            if 0 <= idx < len(hits):
                hit = hits[idx]
                score = float(item.get("relevance_score", 0.0))
                hit.score = score
                hit.metadata = {**hit.metadata, "rerank_score": round(score, 4)}
                ordered.append(hit)
        return ordered if ordered else hits
    except Exception as exc:
        log.debug("rag_rerank_failed err=%s", exc)
        return hits


def _apply_context_budget(hits: list[Hit], max_tokens: int = _CONTEXT_MAX_TOKENS) -> list[Hit]:
    """Truncate *hits* to a hard context-token budget.

    Walks the (reranked) hits in order, accumulating ``estimate_tokens``
    per hit, and keeps the longest prefix that fits within ``max_tokens``.
    At least one hit is always kept so a single oversized chunk still
    surfaces. This guarantees the context handed to the LLM never exceeds
    the budget.
    """
    if max_tokens <= 0:
        return hits
    budgeted: list[Hit] = []
    used = 0
    for h in hits:
        tokens = estimate_tokens(h.text or "")
        if budgeted and used + tokens > max_tokens:
            break
        used += tokens
        budgeted.append(h)
    return budgeted


async def three_pillar_search(
    query: str,
    collection: str = "scam_intel",
    top_k: int = 5,
    min_similarity: float = 0.0,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run the three-pillar search and return RRF-fused, reranked hits.

    Each returned hit: {doc_id, score, text, metadata}

    Primary path (``RAG_STORE=qdrant``, default): the unified Qdrant hybrid
    store (dense + lexical-sparse prefetches, server-side RRF k=60) replaces
    the in-process numpy ANN + Redis BM25-lite scan. The Infinity bge-m3
    embedding (``get_embedding``) still runs here. Cross-encoder rerank
    (Infinity ``BAAI/bge-reranker-v2-m3``) is MANDATORY: every result list
    is reranked before a hard context-token budget (``RAG_CONTEXT_MAX_TOKENS``,
    default 4096) is applied, so the LLM never receives an unbounded
    context. Rerank is fail-open (pre-rerank order kept when the reranker
    is unreachable), but there is no skip path.
    Set ``RAG_STORE=legacy`` to restore the previous numpy/Redis path.
    """
    start = time.monotonic()

    with trace_span(
        "rag.search.three_pillar",
        {
            "openinference.span.kind": "RETRIEVER",
            "openinference.retriever.query": query,
            "retrieval.collection": collection,
            "retrieval.top_k": top_k,
        },
    ) as span:
        # Embed once — shared by the Qdrant primary path and the legacy path.
        qvec = await get_embedding(query)

        if _DEFAULT_STORE == "qdrant":
            fused, backend = await _qdrant_primary_search(query, qvec, collection, top_k, min_similarity, filters)
            if backend == "legacy":
                # Qdrant unavailable or returned nothing → legacy pillars.
                fused = await _legacy_pillar_search(query, qvec, collection, top_k, min_similarity, filters)
        else:
            backend = "legacy"
            fused = await _legacy_pillar_search(query, qvec, collection, top_k, min_similarity, filters)

        # G2: mandatory cross-encoder rerank (fail-open) + hard context-token
        # budget before the LLM call. No skip path.
        pre_rerank = len(fused)
        fused = await _rerank_hits(query, fused)
        rerank_applied = bool(fused and fused[0].metadata.get("rerank_score") is not None)
        span.set_attribute("retrieval.rerank_input", pre_rerank)
        span.set_attribute("retrieval.rerank_applied", rerank_applied)
        fused = _apply_context_budget(fused, max_tokens=_CONTEXT_MAX_TOKENS)
        span.set_attribute("retrieval.context_budget_tokens", _CONTEXT_MAX_TOKENS)

        took_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "rag_search_done collection=%s store=%s fused=%d took_ms=%d backend=%s",
            collection,
            backend,
            len(fused),
            took_ms,
            current_backend(),
        )
        span.set_attribute("openinference.retriever.documents", len(fused))
        span.set_attribute("retrieval.result_count", len(fused))
        span.set_attribute("retrieval.backend", backend)
        span.set_attribute("retrieval.store", backend)
    return [
        {
            "doc_id": h.doc_id,
            "score": h.score,
            "text": h.text,
            "metadata": h.metadata,
        }
        for h in fused
    ]


async def _keyword_search(query: str, collection: str, limit: int) -> list[Hit]:
    """Improved BM25 keyword search with proper tokenization and IDF weighting.

    Uses Okapi BM25 formula with:
      - Tokenisation: lowercase, split on non-alpha, remove stopwords + short terms
      - IDF: inverse document frequency computed from all docs in the collection
      - Length normalisation: average doc length as reference
      - BM25 parameters: k1=1.5, b=0.75 (standard defaults)

    Returns Hits with score in [0, 1] (normalized). While not as good as
    learned sparse retrieval (SPLADE), this significantly outperforms the
    previous TF/length heuristic on precision@K.
    """
    try:
        from rugbrain.storage.cache import get_cache

        r = get_cache()
        if r is None:
            return []
        raw = r.hgetall(f"rag:ann:{collection}:docs")
    except Exception as e:
        log.debug("keyword_search_redis_failed: %s", e)
        return []

    if not raw:
        return []

    import json
    import math
    import re

    # ── Tokenizer ──────────────────────────────────────────────────────
    # Crypto-aware stopwords: keep short token/address terms, filter noise
    _stopwords: set[str] = {
        "the",
        "this",
        "that",
        "and",
        "for",
        "with",
        "from",
        "has",
        "have",
        "was",
        "were",
        "been",
        "are",
        "not",
        "but",
        "its",
        "all",
        "can",
        "will",
        "just",
        "very",
        "also",
        "than",
        "then",
        "each",
        "which",
        "what",
        "when",
        "where",
        "how",
        "who",
    }

    # Sample large collections to prevent OOM — process up to 10K docs max
    max_keyword_docs = 10_000
    if len(raw) > max_keyword_docs:
        import random

        log.info("keyword_search_sampling collection=%s total=%d sample=%d", collection, len(raw), max_keyword_docs)
        raw = dict(random.sample(list(raw.items()), max_keyword_docs))

    def _tokenize(text: str) -> list[str]:
        """Tokenize: lowercase, split on non-alpha, filter stopwords + length."""
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_#]*", text.lower())
        return [t for t in tokens if t not in _stopwords and len(t) > 1]

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    # ── Corpus stats (single pass) ──────────────────────────────────
    doc_list: list[tuple[str, str, dict[str, Any]]] = []  # (doc_id, text, metadata)
    doc_lengths: list[int] = []
    # doc_freq[t] = number of documents containing term t
    doc_freq: dict[str, int] = {}

    for doc_id, blob in raw.items():
        try:
            entry = json.loads(blob)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        text = entry.get("text", "")
        if not text:
            continue
        tokens = _tokenize(text)
        if not tokens:
            continue
        doc_list.append((doc_id, text, entry.get("metadata", {})))
        doc_lengths.append(len(tokens))
        # Unique terms per doc for IDF
        for t in set(tokens):
            doc_freq[t] = doc_freq.get(t, 0) + 1

    if not doc_list:
        return []

    n_docs = len(doc_list)
    avg_dl = sum(doc_lengths) / n_docs

    # ── BM25 scoring ─────────────────────────────────────────────────
    k1 = 1.5
    b = 0.75

    scored: list[tuple[float, str, str, dict[str, Any]]] = []
    for idx, (doc_id, text, metadata) in enumerate(doc_list):
        tokens = _tokenize(text)
        if not tokens:
            continue
        dl = doc_lengths[idx]
        # Term frequency in this doc
        tf_map: dict[str, int] = {}
        for t in tokens:
            tf_map[t] = tf_map.get(t, 0) + 1

        score = 0.0
        for t in query_tokens:
            tf = tf_map.get(t, 0)
            if tf == 0:
                continue
            df = doc_freq.get(t, 1)  # smoothing
            idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
            # BM25 term contribution
            term_score = idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_dl))
            score += term_score

        if score > 0:
            scored.append((score, doc_id, text, metadata))

    # ── Normalise and return ─────────────────────────────────────────
    scored.sort(key=lambda x: -x[0])
    out: list[Hit] = []
    max_score = scored[0][0] if scored else 1.0
    for score, doc_id, text, metadata in scored[:limit]:
        out.append(
            Hit(
                doc_id=doc_id,
                score=min(1.0, score / max_score) if max_score > 0 else 0.0,
                text=text,
                metadata=metadata,
            )
        )
    return out


def _apply_filters(hits: list[Hit], filters: dict[str, Any]) -> list[Hit]:
    """Pre-filter: keep hits whose metadata matches all filter key=val pairs."""
    if not filters:
        return hits
    return [h for h in hits if _matches_filters(h, filters)]


def _matches_filters(hit: Hit, filters: dict[str, Any]) -> bool:
    return all(hit.metadata.get(k) == v for k, v in filters.items())


def _reciprocal_rank_fusion(pillar_hits: list[list[Hit]], top_k: int, k: int = _RRF_K) -> list[Hit]:
    """Reciprocal Rank Fusion across multiple ranked lists.

    RRF score(d) = sum( 1 / (k + rank_i(d)) ) for each pillar that contains d.
    """
    scores: dict[str, float] = {}
    by_id: dict[str, Hit] = {}
    for pillar in pillar_hits:
        for rank, h in enumerate(pillar, start=1):
            scores[h.doc_id] = scores.get(h.doc_id, 0.0) + 1.0 / (k + rank)
            if h.doc_id not in by_id:
                by_id[h.doc_id] = h

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    out: list[Hit] = []
    for doc_id, rrf_score in ranked[:top_k]:
        h = by_id[doc_id]
        # Normalize to [0, 1] roughly - RRF max is ~3/k for 3 pillars
        norm = min(1.0, rrf_score * k / 3.0)
        out.append(
            Hit(
                doc_id=h.doc_id,
                score=norm,
                text=h.text,
                metadata=h.metadata,
            )
        )
    return out


# ── Ingest ───────────────────────────────────────────────────────────
async def ingest_document(
    collection: str,
    doc_id: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    chunk: bool = True,
    content_type: str = "general",
) -> dict[str, Any]:
    """Ingest a document into the RAG system.

    - Chunks the content (content-type-aware strategies, dedup)
    - Embeds each chunk
    - Adds to the collection's ANN index
    - Marks each chunk's hash as ingested for dedup

    ``content_type`` selects the chunking strategy (solidity, whitepaper,
    audit, news, social, transaction, general). Defaults to "general".
    """
    if not content or not content.strip():
        return {"doc_id": doc_id, "collection": collection, "status": "empty", "chunks": 0}

    metadata = metadata or {}
    with trace_span(
        "rag.ingest.document",
        {
            "openinference.span.kind": "CHAIN",
            "ingest.collection": collection,
            "ingest.doc_id": doc_id,
            "ingest.content_type": content_type,
            "ingest.chunk": chunk,
        },
    ) as span:
        idx = get_index(collection)
        if chunk:
            chunks = chunk_text(content, content_type=content_type)
        else:
            # Single-chunk path: still dedup
            chunks = [
                Chunk(
                    text=content,
                    content_hash=content_hash(content),
                    index=0,
                    quality=1.0,
                    content_type=content_type,
                    source_url=metadata.get("source_url", ""),
                    timestamp=datetime.now(UTC).isoformat(),
                    token_count=estimate_tokens(content),
                )
            ]

        # Dedup
        new_chunks = [c for c in chunks if not is_duplicate(c.content_hash, collection)]
        skipped = len(chunks) - len(new_chunks)
        if not new_chunks:
            span.set_attribute("ingest.status", "duplicate")
            span.set_attribute("ingest.skipped", skipped)
            return {
                "doc_id": doc_id,
                "collection": collection,
                "status": "duplicate",
                "chunks": 0,
                "skipped": skipped,
            }

        # Embed + insert
        added = 0
        for c in new_chunks:
            vec = await get_embedding(c.text)
            chunk_doc_id = f"{doc_id}:{c.content_hash[:8]}:{c.index}"
            chunk_meta = {
                **metadata,
                "chunk_index": c.index,
                "content_hash": c.content_hash,
                "quality": c.quality,
                "content_type": c.content_type,
                "source_url": c.source_url,
                "timestamp": c.timestamp,
                "token_count": c.token_count,
            }
            idx.add(chunk_doc_id, vec, chunk_meta, text=c.text)
            mark_ingested(c.content_hash, collection)
            # Qdrant write path (consolidation Phase 1): additive + fail-open.
            # New chunks also land in the unified ``rag_documents`` collection
            # so the Qdrant primary search path stops returning empty (which
            # previously forced the legacy fallback). Legacy writes above stay.
            await _qdrant_upsert_chunk(chunk_doc_id, c, chunk_meta, collection)
            added += 1

        span.set_attribute("ingest.status", "ok")
        span.set_attribute("ingest.chunks_added", added)
        span.set_attribute("ingest.skipped", skipped)

    return {
        "doc_id": doc_id,
        "collection": collection,
        "status": "ok",
        "chunks": added,
        "skipped": skipped,
    }


def _timestamp_epoch(iso_ts: str) -> int:
    """Parse an ISO timestamp to a unix-epoch int (best-effort → now)."""
    try:
        return int(datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).timestamp())
    except (TypeError, ValueError):
        return int(time.time())


async def _qdrant_upsert_chunk(
    chunk_doc_id: str,
    chunk: Chunk,
    chunk_meta: dict[str, Any],
    collection: str,
) -> bool:
    """Upsert one ingested chunk into the unified Qdrant store (additive).

    Fail-open: returns False on any error — the legacy numpy/Redis write
    stays authoritative and the caller's ingest status is unaffected.
    Payload follows the QDRANT-CONSOLIDATION schema: ``content`` (sparse
    source), top-level filter fields (``chain``, ``address``, ``collection``
    provenance, unix timestamps) plus the full ``metadata`` object the
    engine's search path reads back (``app.rag.qdrant_store._point_to_hit``).
    """
    from rugbrain.storage.qdrant_store import upsert_rag_document

    ts_epoch = _timestamp_epoch(chunk.timestamp)
    payload: dict[str, Any] = {
        "doc_id": chunk_doc_id,
        "source": str(chunk_meta.get("source") or collection),
        "content_type": chunk.content_type,
        "content": chunk.text,
        "collection": collection,
        "chain": chunk_meta.get("chain"),
        "address": chunk_meta.get("address"),
        "created_at": ts_epoch,
        "updated_at": ts_epoch,
        "metadata": chunk_meta,
    }
    try:
        ok = await upsert_rag_document(chunk_doc_id, chunk.text, payload)
    except Exception as exc:  # fail-open contract (never raises into ingest)
        log.warning("rag_qdrant_chunk_upsert_failed doc_id=%s err=%s", chunk_doc_id, exc)
        return False
    if not ok:
        log.info("rag_qdrant_chunk_upsert_skipped doc_id=%s", chunk_doc_id)
    return ok


# ── Stats ────────────────────────────────────────────────────────────
def get_collection_stats(collection: str) -> dict[str, Any]:
    """Get stats for one collection: vector count + dedup hashes count."""
    idx = get_index(collection)
    count = idx.count()
    try:
        from rugbrain.storage.cache import get_cache

        client = get_cache()
        hashes = client.scard(f"rag:hashes:{collection}") if client is not None else 0
    except (ConnectionError, TimeoutError, OSError):
        hashes = 0
    return {
        "collection": collection,
        "vector_count": count,
        "dedup_hashes": hashes,
    }


def get_stats(collections: list[str] | None = None) -> dict[str, Any]:
    """Get stats for all (or specified) collections + the active embedder backend."""
    from rugbrain.core.models import COLLECTIONS as DEFAULT_COLLECTIONS

    cols = collections or DEFAULT_COLLECTIONS
    return {
        "total_docs": sum(get_collection_stats(c)["vector_count"] for c in cols),
        "backend": current_backend(),
        "collections": [get_collection_stats(c) for c in cols],
    }


# ── Background helpers (used by ingest_cron worker) ──────────────────
async def bulk_ingest(
    items: list[dict[str, Any]],
    collection: str = "scam_intel",
) -> dict[str, Any]:
    """Ingest many items sequentially. Returns summary counts."""
    if not items:
        return {"total": 0, "ok": 0, "duplicate": 0, "empty": 0, "errors": 0}

    counts = {"total": len(items), "ok": 0, "duplicate": 0, "empty": 0, "errors": 0}
    for it in items:
        try:
            item_meta = it.get("metadata") or {}
            r = await ingest_document(
                collection=collection,
                doc_id=it.get("doc_id") or it.get("id", f"bulk:{int(time.time() * 1000)}"),
                content=it.get("content") or it.get("text", ""),
                metadata=item_meta,
                content_type=(it.get("content_type") or item_meta.get("content_type") or "general"),
            )
            if r["status"] == "ok":
                counts["ok"] += 1
            elif r["status"] == "duplicate":
                counts["duplicate"] += 1
            elif r["status"] == "empty":
                counts["empty"] += 1
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("bulk_ingest_item_failed: %s", e)
            counts["errors"] += 1
    return counts
