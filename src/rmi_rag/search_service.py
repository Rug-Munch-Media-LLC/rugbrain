"""
RAG Ingestion & Retrieval Service v4
====================================
Three-pillar hybrid search (dense + sparse + entity) with:
  - Knowledge Graph expansion (Pillar 3+)
  - MMR deduplication after RRF fusion
  - Parent-child retrieval for contextual chunks
  - SPLADE+BM25 sparse search (Pillar 2)
  - Query-adaptive fusion weights

Collections: wallet_profiles, token_analysis, scam_patterns,
             forensic_reports, market_intel, contract_audits, known_scams
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from rmi_rag.crypto_embeddings import (
    COLLECTIONS,
    KNOWN_SCAM_PATTERNS,
    CryptoEmbedder,
    EmbeddingResult,
    extract_contract_features,
    get_embedder,
)

logger = logging.getLogger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# Track seeding state
_seeded = False

# Scam pattern pre-embedding cache (Feature 3)
_pattern_cache: dict[str, EmbeddingResult] = {}
_pattern_cache_loaded = False

# Redis singleton - reuses connection across all calls
_redis_pool = None


# Singleton accessor for dependency injectiondef get_rag_service():    import sys    return sys.modules[__name__]
async def _get_redis():
    """Get or create a shared Redis connection (singleton pattern to prevent leaks)."""
    global _redis_pool
    import redis.asyncio as redis

    if _redis_pool is not None:
        try:
            await _redis_pool.ping()
        except Exception:
            _redis_pool = None
    if _redis_pool is None:
        _redis_pool = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD or None,
            db=0,
            decode_responses=True,
        )
    return _redis_pool


async def _preembed_scam_patterns():
    """
    Pre-embed all KNOWN_SCAM_PATTERNS and cache results.
    Loads from Redis first (persistent cache), then embeds any missing ones.
    """
    global _pattern_cache, _pattern_cache_loaded

    embedder = await get_embedder()
    r = await _get_redis()

    for pattern in KNOWN_SCAM_PATTERNS:
        pattern_name = pattern["name"]

        # 1. Try in-memory cache
        if pattern_name in _pattern_cache:
            continue

        # 2. Try Redis persistent cache (30-day TTL)
        redis_key = f"rag:pattern_cache:{pattern_name}"
        try:
            cached_data = await r.get(redis_key)
            if cached_data:
                cached = json.loads(cached_data)
                _pattern_cache[pattern_name] = EmbeddingResult(
                    vector=cached["vector"],
                    dims=cached["dims"],
                    model=cached["model"],
                    head=cached.get("head", "scam_pattern"),
                )
                logger.debug("Pattern cache hit (Redis): %s", pattern_name)
                continue
        except Exception as e:
            logger.debug("Redis pattern cache read failed for %s: %s", pattern_name, e)

        # 3. Embed and cache
        try:
            result = await embedder.embed_scam_pattern(
                pattern_name=pattern["name"],
                description=pattern["description"],
                code_snippets=pattern.get("code_snippets", []),
                indicators=pattern.get("indicators", []),
                severity=pattern.get("severity", "high"),
            )
            _pattern_cache[pattern_name] = result

            # Store in Redis with 30-day TTL
            try:
                await r.setex(
                    redis_key,
                    86400 * 30,
                    json.dumps(
                        {
                            "vector": result.vector,
                            "dims": result.dims,
                            "model": result.model,
                            "head": result.head,
                        }
                    ),
                )
            except Exception as e:
                logger.debug("Redis pattern cache write failed for %s: %s", pattern_name, e)

            logger.info("Pre-embedded scam pattern: %s", pattern_name)
        except Exception as e:
            logger.warning("Failed to pre-embed pattern %s: %s", pattern_name, e)

    _pattern_cache_loaded = True


# ═══════════════════════════════════════════════════════════════════
# CORE INGEST
# ═══════════════════════════════════════════════════════════════════


async def ingest_document(
    collection: str,
    content: str,
    metadata: dict | None = None,
    doc_id: str | None = None,
) -> dict:
    """
    Ingest a document with real semantic embedding.
    """
    embedder = await get_embedder()
    metadata = metadata or {}

    if not doc_id:
        doc_id = hashlib.sha256(
            f"{collection}:{content[:100]}:{datetime.now(UTC).isoformat()}".encode()
        ).hexdigest()[:16]

    # Choose embedding head based on collection
    result = await _embed_by_collection(embedder, collection, content, metadata)

    # Store
    r = await _get_redis()
    doc = {
        "id": doc_id,
        "collection": collection,
        "vector": result.vector,
        "dims": result.dims,
        "model": result.model,
        "metadata": {**metadata, "head": result.head},
        "content": content[:5000],
        "stored_at": datetime.now(UTC).isoformat(),
    }
    # TTL by collection type:
    #   Permanent (0 = no expiry): scam_patterns, contract_audits, transaction_patterns, forensic_reports
    #   Long-lived (365 days): wallet_profiles, known_scams, market_intel
    #   Short-lived (30 days): news_articles, general, token_analysis (volatile data)
    _TTL_MAP = {  # noqa: N806 - dict literal in function, uppercase intentional for visibility
        "scam_patterns": 0,
        "contract_audits": 0,
        "transaction_patterns": 0,
        "forensic_reports": 0,
        "wallet_profiles": 86400 * 365,
        "known_scams": 86400 * 365,
        "market_intel": 86400 * 365,
        "news_articles": 86400 * 30,
        "token_analysis": 86400 * 90,
        "general": 86400 * 30,
    }
    ttl = _TTL_MAP.get(collection, 86400 * 30)
    key = f"rag:{collection}:{doc_id}"
    if ttl == 0:
        await r.set(key, json.dumps(doc))  # permanent, no expiry
    else:
        await r.setex(key, ttl, json.dumps(doc))
    await r.sadd(f"rag:idx:{collection}", doc_id)

    # Bump ANN index version so it gets rebuilt on next search
    try:
        from app.ann_index import get_ann_index  # external dependency

        ann = get_ann_index()
        await ann.bump_version(collection)
    except Exception as e:
        logger.debug("ANN version bump skipped: %s", e)

    # ── Update secondary indexes ──
    # BM25 index: invalidate cache so it gets rebuilt
    try:
        from rmi_rag.splade_bm25 import _bm25_built_at, _bm25_index

        if _bm25_index is not None:
            # Invalidate cache so next search rebuilds
            _bm25_built_at = 0  # force rebuild on next call
            logger.debug("BM25 index cache invalidated for incremental rebuild")
    except Exception as e:
        logger.debug("BM25 cache invalidation skipped: %s", e)

    # Entity extraction + indexing
    try:
        from app.entity_extraction import EntityLookup, extract_entities  # external dependency

        entities = extract_entities(content)
        if entities.has_entities:
            lookup = EntityLookup.get_instance()
            for entity in entities.all_entities():
                await lookup.index_entity(
                    entity["type"],
                    entity["value"],
                    doc_id,
                    metadata={**metadata, "collection": collection},
                )
            logger.debug("Entity indexing: %s entities", len(entities.all_entities()))
    except Exception as e:
        logger.debug("Entity indexing skipped: %s", e)

    # Knowledge Graph edge creation
    try:
        from app.knowledge_graph import get_knowledge_graph  # external dependency

        kg = await get_knowledge_graph()
        await kg.ingest_rag_document(collection, doc_id, content, metadata)
        logger.debug("KG edge creation complete")
    except Exception as e:
        logger.debug("KG edge creation skipped: %s", e)

    # pgvector disabled - FAISS + Redis are the primary vector stores.
    # Supabase pgvector was a migration artifact consuming 500MB of free-tier quota.
    # All vector search goes through FAISS ANN → Redis hydration.
    logger.debug("pgvector upsert skipped (FAISS primary)")

    # Store parent document for context expansion
    try:
        from app.rag.parent_retriever import get_parent_retriever  # external dependency

        pr = await get_parent_retriever()
        await pr.store_parent(doc_id=doc_id, content=content, metadata=metadata)
    except Exception as e:
        logger.debug("ParentRetriever.store_parent skipped: %s", e)

    logger.info("Ingested %s/%s: %s...", collection, doc_id, content[:60])
    return {"id": doc_id, "dims": result.dims, "collection": collection}


async def _embed_by_collection(
    embedder: CryptoEmbedder,
    collection: str,
    content: str,
    metadata: dict,
) -> EmbeddingResult:
    """Route to the right embedding head based on collection type."""
    if collection == "scam_patterns" or collection == "known_scams":
        return await embedder.embed_scam_pattern(
            pattern_name=metadata.get("name", content[:50]),
            description=content,
            severity=metadata.get("severity", "medium"),
        )
    elif collection == "token_analysis":
        code = metadata.get("contract_code", "")
        return await embedder.embed_token_scam(
            name=metadata.get("name", ""),
            symbol=metadata.get("symbol", ""),
            description=content,
            contract_code=code,
            chain=metadata.get("chain", "solana"),
            metadata=metadata,
        )
    elif collection == "wallet_profiles":
        return await embedder.embed_wallet(
            address=metadata.get("address", ""),
            labels=metadata.get("labels", []),
            transactions=metadata.get("transactions", []),
            chain=metadata.get("chain", "solana"),
            balance_usd=float(metadata.get("balance_usd", 0) or 0),
        )
    elif collection == "contract_audits":
        vec = await embedder._semantic_embed_one(content, "semantic")
        return EmbeddingResult(
            vector=vec,
            dims=len(vec),
            model="local/bge-small-en-v1.5",
            head="contract_audit",
        )
    else:
        # Generic semantic
        vec = await embedder._semantic_embed_one(content, "semantic")
        return EmbeddingResult(
            vector=vec,
            dims=len(vec),
            model="openai/text-embedding-3-large",
            head="semantic",
        )


# ═══════════════════════════════════════════════════════════════════
# SEARCH - now backed by FAISS ANN index + semantic cache
# ═══════════════════════════════════════════════════════════════════


async def search_similar(
    query: str,
    collection: str = "wallet_profiles",
    limit: int = 10,
    min_similarity: float = 0.0,
) -> list[dict[str, Any]]:
    """
    Semantic search across any collection.
    Uses FAISS ANN index for fast retrieval, with semantic cache.
    """
    import time

    t0 = time.time()

    embedder = await get_embedder()
    query_vec = await embedder.embed_query(query)

    # 1. Check semantic cache
    cache_hit = False
    try:
        from app.semantic_cache import get_semantic_cache  # external dependency

        cache = get_semantic_cache()
        cached = await cache.check(query_vec)
        if cached is not None:
            cache_hit = True
            logger.info("Semantic cache hit for query: %s", query[:60])
            # Trace observability
            try:
                from rmi_rag.observability import trace_retrieval  # external dependency

                trace_retrieval((time.time() - t0) * 1000, collection, len(cached), cache_hit=True)
            except Exception:
                logging.getLogger(__name__).warning("swallowed exception", exc_info=True)
            return cached
    except Exception as e:
        logger.debug("Semantic cache check skipped: %s", e)

    # 2. Try FAISS ANN index search
    results = []
    try:
        from app.ann_index import get_ann_index  # external dependency

        ann = get_ann_index()
        if ann.is_built(collection):
            ann_results = await ann.search(
                query_embedding=query_vec,
                collection=collection,
                limit=limit,
                min_similarity=min_similarity,
            )
            if ann_results:
                # ANN search already hydrates content; skip re-enrichment for hits that have content
                needs_enrich = [r for r in ann_results if "content" not in r]
                enriched = (
                    await _enrich_ann_results(collection, needs_enrich) if needs_enrich else []
                )
                results = [r for r in ann_results if "content" in r] + enriched
        else:
            # FAISS not built - fall back to embedder brute-force search
            logger.info("FAISS not built for %s, using brute-force fallback", collection)
            results = await embedder.search(
                query=query,
                collection=collection,
                limit=limit,
                min_similarity=min_similarity,
            )
    except Exception as e:
        logger.warning("ANN/pgvector search failed, falling back to brute-force: %s", e)
        # 3. Fallback to embedder brute-force search
        results = await embedder.search(
            query=query,
            collection=collection,
            limit=limit,
            min_similarity=min_similarity,
        )

    # 4. Store in semantic cache
    if results:
        try:
            from app.semantic_cache import get_semantic_cache  # external dependency

            cache = get_semantic_cache()
            await cache.store(query_vec, results)
        except Exception as e:
            logger.debug("Semantic cache store skipped: %s", e)

    # Trace observability
    try:
        from rmi_rag.observability import trace_retrieval  # external dependency

        trace_retrieval((time.time() - t0) * 1000, collection, len(results), cache_hit=cache_hit)
    except Exception:
        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    return results


async def _enrich_ann_results(
    collection: str,
    ann_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Given ANN results (id + similarity), fetch full documents from Redis
    and merge the content/metadata.
    """
    if not ann_results:
        return []

    r = await _get_redis()
    pipe = r.pipeline()
    for ar in ann_results:
        pipe.get(f"rag:{collection}:{ar['id']}")

    raw_docs = await pipe.execute()
    enriched = []
    for ar, data in zip(ann_results, raw_docs, strict=False):
        if not data:
            # Return what we have even without enrichment
            enriched.append(ar)
            continue
        try:
            doc = json.loads(data)
            enriched.append(
                {
                    "id": doc.get("id", ar["id"]),
                    "similarity": ar["similarity"],
                    "content": doc.get("content", "")[:500],
                    "metadata": doc.get("metadata", {}),
                    "model": doc.get("model", ""),
                    "collection": doc.get("collection", collection),
                }
            )
        except json.JSONDecodeError:
            enriched.append(ar)

    return enriched


async def search_multi_collection(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.5,
) -> list[dict[str, Any]]:
    """Search across multiple collections and merge results."""
    if collections is None:
        collections = COLLECTIONS

    async def _search_one(coll: str) -> list[dict[str, Any]]:
        try:
            results = await search_similar(
                query, collection=coll, limit=limit, min_similarity=min_similarity
            )
            for r in results:
                if "collection" not in r:
                    r["collection"] = coll
            return results
        except Exception as e:
            logger.warning("Search failed for %s: %s", coll, e)
            return []

    # Search all collections in parallel
    all_per_collection = await asyncio.gather(*[_search_one(c) for c in collections])
    all_results = [r for results in all_per_collection for r in results]

    all_results.sort(key=lambda x: x["similarity"], reverse=True)
    return all_results[:limit]


# ═══════════════════════════════════════════════════════════════════
# THREE-PILLAR HYBRID SEARCH
# ═══════════════════════════════════════════════════════════════════


async def three_pillar_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.5,
    entity_boost: float = 1.5,
    use_mmr: bool = True,
    mmr_lambda: float = 0.6,
    use_kg: bool = True,
    use_parent_child: bool = True,
    use_reranker: bool = False,
) -> dict[str, Any]:
    """
    Three-pillar hybrid search with Knowledge Graph expansion, MMR dedup,
    and parent-child retrieval.

      Pillar 1 - Dense vector search via FAISS ANN index (semantic similarity)
      Pillar 2 - Sparse text search via SPLADE+BM25
      Pillar 3 - Entity exact-match + Knowledge Graph expansion
      Post-fusion - MMR deduplication + parent-child context expansion

    All pillar result sets are fused with Reciprocal Rank Fusion (k=60),
    then deduplicated with Maximal Marginal Relevance for diversity,
    and expanded with parent-child context for generation quality.

    Returns dict with merged results + pillar attribution + MMR metadata.
    """
    if collections is None:
        collections = COLLECTIONS

    # Cap limit to prevent unbounded MMR/parent-child expansion
    limit = min(limit, 100)

    embedder = await get_embedder()
    query_vec = await embedder.embed_query(query)

    # ── Pillar 1: Dense vector search (ANN) ──
    pillar1_results: list[dict[str, Any]] = []
    try:
        # Check semantic cache first
        from app.semantic_cache import get_semantic_cache  # external dependency

        cache = get_semantic_cache()
        cached = await cache.check(query_vec)
        if cached is not None:
            pillar1_results = cached
            for r in pillar1_results:
                r["pillar"] = "dense (cached)"
        else:
            # ANN search across collections
            for coll in collections:
                try:
                    from app.ann_index import get_ann_index  # external dependency

                    ann = get_ann_index()
                    ann_hits = await ann.search(
                        query_embedding=query_vec,
                        collection=coll,
                        limit=limit,
                        min_similarity=min_similarity,
                    )
                    enriched = await _enrich_ann_results(coll, ann_hits)
                    for r in enriched:
                        r["pillar"] = "dense"
                    pillar1_results.extend(enriched)
                except Exception as e:
                    logger.debug("ANN search for %s failed: %s", coll, e)
                    # Fallback to embedder search for this collection
                    try:
                        hits = await embedder.search(
                            query, coll, limit=limit, min_similarity=min_similarity
                        )
                        for r in hits:
                            r["pillar"] = "dense"
                        pillar1_results.extend(hits)
                    except Exception:
                        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

            # Store in semantic cache
            if pillar1_results:
                await cache.store(query_vec, pillar1_results)
    except Exception as e:
        logger.warning("Pillar 1 (dense) failed: %s", e)
        # Fallback: use search_multi_collection
        try:
            pillar1_results = await search_multi_collection(
                query, collections, limit=limit * 2, min_similarity=min_similarity
            )
            for r in pillar1_results:
                r["pillar"] = "dense"
        except Exception:
            logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    # ── Pillar 2: Sparse text search (BM25 + SPLADE) ──
    pillar2_results: list[dict[str, Any]] = []
    try:
        from rmi_rag.splade_bm25 import splade_search

        pillar2_results = await splade_search(query, collections, limit=limit * 2)
        for r in pillar2_results:
            r["pillar"] = "sparse"
    except ImportError:
        # Fallback to old sparse search if new module unavailable
        try:
            pillar2_results = await _sparse_text_search(query, collections, limit=limit * 2)
            for r in pillar2_results:
                r["pillar"] = "sparse"
        except Exception as e:
            logger.warning("Pillar 2 (sparse) failed: %s", e)
    except Exception as e:
        logger.warning("Pillar 2 (SPLADE) failed: %s", e)
        # Fallback to old sparse search
        try:
            pillar2_results = await _sparse_text_search(query, collections, limit=limit * 2)
            for r in pillar2_results:
                r["pillar"] = "sparse"
        except Exception as e2:
            logger.warning("Pillar 2 fallback also failed: %s", e2)

    # ── Pillar 3: Entity exact-match + Knowledge Graph expansion ──
    pillar3_results: list[dict[str, Any]] = []
    entity_extraction = None
    try:
        from app.entity_extraction import EntityLookup, extract_entities  # external dependency

        entity_extraction = extract_entities(query)
        if entity_extraction.has_entities:
            lookup = EntityLookup.get_instance()
            for entity in entity_extraction.all_entities():
                try:
                    matches = await lookup.lookup(entity["value"])
                    for m in matches:
                        m["pillar"] = "entity"
                    pillar3_results.extend(matches)
                except Exception as e:
                    logger.debug("Entity lookup for %s failed: %s", entity['value'], e)

            # Knowledge Graph expansion: find related entities via graph traversal
            if use_kg:
                try:
                    from app.knowledge_graph import get_knowledge_graph  # external dependency

                    kg = await get_knowledge_graph()
                    kg_results = await kg.expand_query_entities(
                        entity_extraction.all_entities(), max_depth=2
                    )
                    if kg_results:
                        for r in kg_results:
                            r["pillar"] = "entity+kg"
                        pillar3_results.extend(kg_results)
                        logger.info("KG expansion: %s related entities", len(kg_results))
                except ImportError:
                    logger.debug("Knowledge Graph module not available, skipping KG expansion")
                except Exception as e:
                    logger.debug("KG expansion failed (non-critical): %s", e)

    except Exception as e:
        logger.warning("Pillar 3 (entity) failed: %s", e)

    # ── RRF fusion with query-adaptive weights ──
    # Classify query type to weight pillars appropriately
    query_type = _classify_query(query)
    # Dense (semantic) weight, Sparse (keyword) weight, Entity weight
    WEIGHTS = {  # noqa: N806 - dict literal in function, uppercase intentional for visibility
        "entity_heavy": (0.3, 0.2, 0.5),  # Address/symbol queries → entity-heavy
        "keyword_heavy": (0.3, 0.6, 0.1),  # Keyword/term queries → sparse-heavy
        "semantic_heavy": (0.7, 0.2, 0.1),  # Natural language → dense-heavy
        "balanced": (0.4, 0.4, 0.2),  # Mixed queries → balanced
    }
    w_dense, w_sparse, w_entity = WEIGHTS.get(query_type, WEIGHTS["balanced"])

    p1_ranked = sorted(pillar1_results, key=lambda x: x.get("similarity", 0), reverse=True)
    p2_ranked = sorted(
        pillar2_results,
        key=lambda x: x.get("text_score", 0) or x.get("sparse_score", 0) or x.get("bm25_score", 0),
        reverse=True,
    )
    p3_ranked = sorted(pillar3_results, key=lambda x: x.get("score", 0), reverse=True)

    entity_doc_ids = set()
    for r in pillar3_results:
        did = r.get("doc_id") or r.get("id")
        if did:
            entity_doc_ids.add(did)

    fused = _rrf_fuse(
        [p1_ranked, p2_ranked, p3_ranked],
        k=60,
        entity_boost=entity_boost,
        entity_doc_ids=entity_doc_ids,
        pillar_weights=[w_dense, w_sparse, w_entity],
    )

    # Add pillar attribution
    pillar_map: dict[str, list[str]] = {}
    for r in pillar1_results:
        did = r.get("id") or r.get("doc_id")
        if did:
            pillar_map.setdefault(did, [])
            if "dense" not in pillar_map[did]:
                pillar_map[did].append("dense")
    for r in pillar2_results:
        did = r.get("id") or r.get("doc_id")
        if did:
            pillar_map.setdefault(did, [])
            if "sparse" not in pillar_map[did]:
                pillar_map[did].append("sparse")
    for r in pillar3_results:
        did = r.get("id") or r.get("doc_id")
        if did:
            pillar_map.setdefault(did, [])
            if "entity" not in pillar_map[did]:
                pillar_map[did].append("entity")

    for r in fused:
        did = r.get("id") or r.get("doc_id")
        r["pillars"] = pillar_map.get(did, ["dense"])
        r["match_type"] = "+".join(r["pillars"])

    # ── MMR deduplication ──
    pre_mmr_count = len(fused)
    mmr_applied = False
    if use_mmr and len(fused) > limit:
        try:
            from app.mmr_dedup import mmr_dedup_results  # external dependency

            fused = await mmr_dedup_results(
                fused,
                query_score_field="rrf_score",
                content_field="content",
                lambda_param=mmr_lambda,
                top_k=limit * 3,  # Keep more for parent-child expansion
            )
            mmr_applied = True
            logger.info("MMR dedup: %s → %s results (lambda=%s)", pre_mmr_count, len(fused), mmr_lambda)
        except ImportError:
            logger.debug("MMR dedup module not available, skipping")
        except Exception as e:
            logger.debug("MMR dedup failed (non-critical): %s", e)

    # ── Cross-encoder reranking (optional stage after MMR) ──
    # Two-stage fallback: 1) Ollama bge-m3 cosine reranker (lightweight),
    # 2) sentence-transformers CrossEncoder (full joint scoring).
    reranker_applied = False
    reranker_method: str | None = None
    if use_reranker and fused:
        for r in fused:
            r["similarity"] = r.get("rrf_score", 0.0)
        pre_rerank = len(fused)

        # Stage 1: Ollama bge-m3 cosine approximation
        try:
            from rmi_rag.cross_encoder import (
                get_reranker as _get_ollama_reranker,  # external dependency
            )

            ollama_reranker = await _get_ollama_reranker()
            fused = await ollama_reranker.rerank(query, fused, top_k=limit * 3)
            reranker_applied = True
            reranker_method = "ollama-cosine"
            logger.info(
                "Cross-encoder (ollama) reranked: %d → %d results",
                pre_rerank,
                len(fused),
            )
        except ImportError:
            logger.debug("app.rag.cross_encoder not available")
        except Exception as e:
            logger.debug("Ollama reranker failed (non-critical): %s", e)

        # Stage 2: sentence-transformers CrossEncoder fallback
        if not reranker_applied:
            try:
                from app.cross_encoder_reranker import get_reranker  # external dependency

                reranker = await get_reranker()
                fused = await reranker.rerank(query, fused, top_k=limit * 3)
                reranker_applied = True
                reranker_method = "cross-encoder"
                logger.info(
                    "Cross-encoder (sentence-transformers) reranked: %d → %d results",
                    pre_rerank,
                    len(fused),
                )
            except ImportError:
                logger.debug("Cross-encoder module not available, skipping rerank")
            except Exception as e:
                logger.debug("Cross-encoder rerank failed (non-critical): %s", e)

    # ── Parent-child context expansion ──
    parent_child_applied = False
    if use_parent_child and fused:
        try:
            from rmi_rag.contextual_chunking import parent_child_chunk  # noqa: F401

            expanded_results = []
            for r in fused[: limit * 2]:
                content = r.get("content", "") or ""
                metadata = r.get("metadata", {}) or {}

                # If the result has a parent document reference, try to expand
                parent_id = metadata.get("parent_id") or r.get("parent_id")
                if parent_id:
                    # Fetch parent content from Redis
                    try:
                        pr = await _get_redis()
                        coll = r.get("collection", "wallet_profiles")
                        parent_data = await pr.get(f"rag:{coll}:{parent_id}")
                        if parent_data:
                            pdoc = json.loads(parent_data)
                            r["parent_content"] = pdoc.get("content", "")[:2000]
                            r["parent_id"] = parent_id
                            parent_child_applied = True
                    except Exception:
                        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

                # If content is short, it might be a child chunk - include for generation
                if content and len(content) < 800:
                    r["chunk_type"] = "child"
                    r["retrieval_note"] = "Short chunk - consider parent context for generation"

                expanded_results.append(r)

            if expanded_results:
                fused = expanded_results
        except ImportError:
            logger.debug("Contextual chunking module not available")
        except Exception as e:
            logger.debug("Parent-child expansion failed (non-critical): %s", e)

    # ── Parent document context expansion (ParentRetriever) ──
    try:
        from app.rag.parent_retriever import get_parent_retriever  # external dependency

        pr = await get_parent_retriever()
        fused = await pr.expand_context(fused)
        if fused:
            parent_child_applied = True
    except ImportError:
        logger.debug("ParentRetriever module not available")
    except Exception as e:
        logger.debug("ParentRetriever.expand_context failed (non-critical): %s", e)

    # Final trim
    final_results = fused[:limit]

    # ── Confidence scoring ──
    confidence = None
    try:
        from app.confidence import score_confidence  # external dependency

        entity_list = list(entity_doc_ids)[:5] if entity_doc_ids else []
        confidence = score_confidence(final_results, query=query, entity_matches=entity_list)
    except ImportError:
        logger.debug("Confidence module not available")
    except Exception as e:
        logger.debug("Confidence scoring failed (non-critical): %s", e)

    return {
        "results": final_results,
        "pillar_summary": {
            "dense_hits": len(pillar1_results),
            "sparse_hits": len(pillar2_results),
            "entity_hits": len(pillar3_results),
            "entity_doc_ids": list(entity_doc_ids),
            "pillars_used": [
                p
                for p, n in [
                    ("dense", len(pillar1_results)),
                    ("sparse", len(pillar2_results)),
                    ("entity", len(pillar3_results)),
                ]
                if n > 0
            ],
            "mmr_applied": mmr_applied,
            "mmr_lambda": mmr_lambda if mmr_applied else None,
            "pre_mmr_count": pre_mmr_count,
            "post_mmr_count": len(fused) if mmr_applied else pre_mmr_count,
            "kg_expansion": len([r for r in pillar3_results if r.get("pillar") == "entity+kg"]),
            "parent_child_applied": parent_child_applied,
            "reranker_applied": reranker_applied,
            "reranker_method": reranker_method,
        },
        "entity_extraction": entity_extraction.to_dict() if entity_extraction else None,
        "query": query,
        "query_type": query_type,
        "fusion_weights": {"dense": w_dense, "sparse": w_sparse, "entity": w_entity},
        "collections": collections,
        "confidence": confidence,
    }


def _classify_query(query: str) -> str:
    """Classify a query into entity_heavy, keyword_heavy, semantic_heavy, or balanced."""
    q = query.lower().strip()

    # Entity-heavy: contains hex addresses, $tickers, or known crypto symbols
    if re.search(r"0x[a-f0-9]{6,}", q):
        return "entity_heavy"
    if re.search(r"\$[a-z]{2,10}", q):
        return "entity_heavy"

    # Keyword-heavy: short, term-based, no natural language flow
    words = q.split()
    if len(words) <= 3 and not any(
        w in q for w in ["what", "how", "why", "when", "explain", "describe", "compare", "analyze"]
    ):
        return "keyword_heavy"

    # Semantic-heavy: natural language questions
    if any(
        w in q
        for w in [
            "what is",
            "how does",
            "why did",
            "explain",
            "describe",
            "compare",
            "analyze",
            "tell me",
            "show me",
            "find",
        ]
    ):
        return "semantic_heavy"

    return "balanced"


def _rrf_fuse(
    ranked_lists: list[list[dict[str, Any]]],
    k: int = 60,
    entity_boost: float = 1.5,
    entity_doc_ids: set | None = None,
    pillar_weights: list[float] | None = None,
) -> list[dict[str, Any]]:
    """
    Reciprocal Rank Fusion of multiple result lists.
    Supports pillar-specific weights for query-adaptive fusion.
    """
    entity_doc_ids = entity_doc_ids or set()
    if pillar_weights is None:
        pillar_weights = [1.0] * len(ranked_lists)

    scores: dict[str, float] = {}
    doc_data: dict[str, dict[str, Any]] = {}

    for list_idx, rlist in enumerate(ranked_lists):
        weight = pillar_weights[list_idx] if list_idx < len(pillar_weights) else 1.0
        for rank, item in enumerate(rlist, start=1):
            doc_id = item.get("doc_id") or item.get("id")
            if not doc_id:
                continue
            rrf = weight / (k + rank)
            if doc_id in entity_doc_ids:
                rrf *= entity_boost
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf
            if doc_id not in doc_data:
                doc_data[doc_id] = item

    sorted_ids = sorted(scores, key=lambda d: scores[d], reverse=True)
    results = []
    for doc_id in sorted_ids:
        entry = dict(doc_data[doc_id])
        entry["rrf_score"] = round(scores[doc_id], 6)
        entry["entity_match"] = doc_id in entity_doc_ids
        results.append(entry)

    return results


# ═══════════════════════════════════════════════════════════════════
# QUERY TRANSFORMATION SEARCH
# ═══════════════════════════════════════════════════════════════════


async def search_with_transform(
    query: str,
    collection: str = "wallet_profiles",
    limit: int = 10,
    strategy: str = "auto",
) -> dict[str, Any]:
    """
    Search with query transformation.
    Transforms the query first, then searches with all transformed queries,
    and merges results using RRF.
    """
    from app.query_transform import transform_query  # external dependency

    transformed = await transform_query(query, strategy=strategy)

    # Search with each transformed query
    all_result_lists = []
    for tq in transformed.transformed_queries:
        try:
            results = await search_similar(
                tq, collection=collection, limit=limit, min_similarity=0.3
            )
            if results:
                all_result_lists.append(results)
        except Exception as e:
            logger.debug("Transformed query search failed for '%s': %s", tq[:60], e)

    # If only one list (or none), just return as-is
    if len(all_result_lists) <= 1:
        results = all_result_lists[0] if all_result_lists else []
        return {
            "query": query,
            "collection": collection,
            "results": results[:limit],
            "total": len(results),
            "strategy": transformed.strategy,
            "transformed_queries": transformed.transformed_queries,
            "metadata": transformed.metadata,
        }

    # RRF merge across result lists
    rrf_k = 60
    scores: dict[str, float] = {}
    doc_data: dict[str, dict[str, Any]] = {}

    for rlist in all_result_lists:
        sorted_list = sorted(rlist, key=lambda x: x.get("similarity", 0), reverse=True)
        for rank, item in enumerate(sorted_list, start=1):
            doc_id = item.get("doc_id") or item.get("id")
            if not doc_id:
                continue
            rrf = 1.0 / (rrf_k + rank)
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf
            if doc_id not in doc_data:
                doc_data[doc_id] = item

    sorted_ids = sorted(scores, key=lambda d: scores[d], reverse=True)
    merged = []
    for doc_id in sorted_ids:
        entry = dict(doc_data[doc_id])
        entry["rrf_score"] = round(scores[doc_id], 6)
        merged.append(entry)

    return {
        "query": query,
        "collection": collection,
        "results": merged[:limit],
        "total": len(merged),
        "strategy": transformed.strategy,
        "transformed_queries": transformed.transformed_queries,
        "metadata": transformed.metadata,
    }


async def _sparse_text_search(
    query: str,
    collections: list[str],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Simple keyword-based text search over Redis-stored documents.
    Tokenizes the query and matches against content fields.
    This is a lightweight BM25-style fallback when pgvector FTS is not available.
    """
    r = await _get_redis()

    # Tokenize query
    query_tokens = set(query.lower().split())
    if not query_tokens:
        return []

    results = []
    for coll in collections:
        try:
            doc_ids = list(await r.smembers(f"rag:idx:{coll}"))
            if not doc_ids:
                continue

            # Batch fetch (limit to avoid massive reads)
            sample_ids = doc_ids[:200]
            keys = [f"rag:{coll}:{did}" for did in sample_ids]
            pipe = r.pipeline()
            for k in keys:
                pipe.get(k)
            raw_docs = await pipe.execute()

            for data in raw_docs:
                if not data:
                    continue
                try:
                    doc = json.loads(data)
                except json.JSONDecodeError:
                    continue

                content = doc.get("content", "").lower()
                if not content:
                    continue

                # BM25-style scoring: count matching tokens
                content_tokens = set(content.split())
                matching = query_tokens & content_tokens
                if not matching:
                    continue

                # Simple BM25-ish score: match ratio * idf-like factor
                score = len(matching) / (len(query_tokens) + 0.5)
                # Boost exact phrase matches
                if query.lower() in content:
                    score *= 2.0

                results.append(
                    {
                        "id": doc.get("id", ""),
                        "doc_id": doc.get("id", ""),
                        "text_score": round(score, 4),
                        "content": doc.get("content", "")[:500],
                        "metadata": doc.get("metadata", {}),
                        "collection": coll,
                    }
                )
        except Exception as e:
            logger.debug("Sparse search for %s failed: %s", coll, e)

    results.sort(key=lambda x: x["text_score"], reverse=True)
    return results[:limit]


# ═══════════════════════════════════════════════════════════════════
# SCAM PATTERN DETECTION
# ═══════════════════════════════════════════════════════════════════


async def detect_scam_patterns(
    token_data: dict,
    threshold: float = 0.65,
) -> dict[str, Any]:
    """
    Compare a token against all known scam patterns.
    Returns matched patterns with similarity scores.
    """
    global _pattern_cache_loaded
    embedder = await get_embedder()

    # Pre-embed patterns on first call if not already done
    if not _pattern_cache_loaded:
        await _preembed_scam_patterns()

    # Build a token embedding
    code = token_data.get("contract_code", "")
    desc = token_data.get("description", "")
    name = token_data.get("name", "Unknown")
    symbol = token_data.get("symbol", "???")

    # Quick pre-filter: check code for exact keyword matches (fast, no API)
    code_lower = code.lower() if code else ""
    quick_matches = []
    for pattern in KNOWN_SCAM_PATTERNS:
        if not pattern["code_snippets"]:
            continue
        hits = sum(1 for s in pattern["code_snippets"] if s.lower() in code_lower)
        if hits > 0:
            quick_matches.append(
                {
                    "pattern": pattern["name"],
                    "severity": pattern["severity"],
                    "quick_match_score": hits / len(pattern["code_snippets"]),
                    "matched_snippets": hits,
                }
            )

    # Deep semantic comparison
    token_result = await embedder.embed_token_scam(
        name=name,
        symbol=symbol,
        description=desc,
        contract_code=code,
        chain=token_data.get("chain", "solana"),
        metadata=token_data,
    )

    # Vector layout:
    #   token  = [semantic(sem_dim) | code(128) | behavioral(64) | wallet(64)]
    #   pattern = [semantic(sem_dim) | code(128)]
    # Both have a common [semantic | code] prefix if they use the same model.
    # For the semantic comparison, use only the common prefix.
    # For the code comparison, compare the 128-dim code sections.

    token_total = len(token_result.vector)
    # If token vector has multi-head layout, extract heads by known offsets
    if token_total > 128 + 64 + 64:
        # Full multi-head: [semantic | code(128) | behavioral(64) | wallet(64)]
        token_sem_end = token_total - 128 - 64 - 64
        token_sem = token_result.vector[:token_sem_end]
        token_code = token_result.vector[token_sem_end : token_sem_end + 128]
    elif token_total > 128:
        # Partial: [semantic | code(128)] - no behavioral/wallet
        token_sem_end = token_total - 128
        token_sem = token_result.vector[:token_sem_end]
        token_code = token_result.vector[token_sem_end : token_sem_end + 128]
    else:
        # Only semantic - use as-is
        token_sem = token_result.vector
        token_code = [0.0] * 128

    deep_matches = []
    for pattern in KNOWN_SCAM_PATTERNS:
        # Use pre-embedded cache if available, otherwise embed on-demand and cache
        pattern_name = pattern["name"]
        if pattern_name in _pattern_cache:
            pattern_result = _pattern_cache[pattern_name]
        else:
            pattern_result = await embedder.embed_scam_pattern(
                pattern_name=pattern["name"],
                description=pattern["description"],
                code_snippets=pattern.get("code_snippets", []),
                indicators=pattern.get("indicators", []),
                severity=pattern.get("severity", "high"),
            )
            # Cache for future use
            _pattern_cache[pattern_name] = pattern_result
            # Also persist to Redis
            try:
                r = await _get_redis()
                redis_key = f"rag:pattern_cache:{pattern_name}"
                await r.setex(
                    redis_key,
                    86400 * 30,
                    json.dumps(
                        {
                            "vector": pattern_result.vector,
                            "dims": pattern_result.dims,
                            "model": pattern_result.model,
                            "head": pattern_result.head,
                        }
                    ),
                )
            except Exception:
                logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

        # Extract pattern semantic + code portions
        pat_total = len(pattern_result.vector)
        if pat_total > 128:
            pat_sem_end = pat_total - 128
            pattern_sem = pattern_result.vector[:pat_sem_end]
            pattern_result.vector[pat_sem_end : pat_sem_end + 128]
        else:
            pattern_sem = pattern_result.vector

        # Compare same-length semantic portions (may differ if different models)
        min_sem = min(len(token_sem), len(pattern_sem))
        if min_sem == 0:
            continue
        sem_sim = embedder.cosine_similarity(token_sem[:min_sem], pattern_sem[:min_sem])

        # Code similarity
        pat_code_features = (
            extract_contract_features("\n".join(pattern.get("code_snippets", []))).tolist()
            if pattern.get("code_snippets")
            else [0.0] * 128
        )
        code_sim = (
            embedder.cosine_similarity(token_code, pat_code_features)
            if any(pat_code_features)
            else 0.0
        )

        combined = 0.7 * sem_sim + 0.3 * code_sim

        if combined >= threshold:
            deep_matches.append(
                {
                    "pattern": pattern["name"],
                    "severity": pattern["severity"],
                    "description": pattern["description"],
                    "similarity": round(combined, 4),
                    "semantic_sim": round(sem_sim, 4),
                    "code_sim": round(code_sim, 4),
                }
            )

    deep_matches.sort(key=lambda x: x["similarity"], reverse=True)

    return {
        "quick_matches": quick_matches,
        "deep_matches": deep_matches,
        "highest_threat": deep_matches[0]["pattern"] if deep_matches else "none",
        "threat_severity": deep_matches[0]["severity"] if deep_matches else "low",
    }


# ═══════════════════════════════════════════════════════════════════
# SEEDING
# ═══════════════════════════════════════════════════════════════════


async def seed_known_scams() -> dict[str, Any]:
    """Seed the RAG database with known scam patterns."""
    global _seeded

    r = await _get_redis()
    existing = await r.scard("rag:idx:known_scams")
    if existing > 0 and _seeded:
        return {"status": "already_seeded", "count": existing}

    embedder = await get_embedder()
    count = 0

    for pattern in KNOWN_SCAM_PATTERNS:
        try:
            pid = hashlib.sha256(f"scam:{pattern['name']}".encode()).hexdigest()[:16]
            result = await embedder.embed_scam_pattern(
                pattern_name=pattern["name"],
                description=pattern["description"],
                code_snippets=pattern.get("code_snippets", []),
                indicators=pattern.get("indicators", []),
                severity=pattern.get("severity", "high"),
            )

            doc = {
                "id": pid,
                "collection": "known_scams",
                "vector": result.vector,
                "dims": result.dims,
                "model": result.model,
                "metadata": pattern,
                "content": pattern["description"],
                "stored_at": datetime.now(UTC).isoformat(),
            }
            key = f"rag:known_scams:{pid}"
            await r.set(key, json.dumps(doc))  # permanent - seed data should never expire
            await r.sadd("rag:idx:known_scams", pid)
            count += 1
            logger.info("Seeded scam pattern: %s", pattern['name'])

        except Exception as e:
            logger.error("Failed to seed %s: %s", pattern['name'], e)

    _seeded = True
    return {
        "status": "seeded",
        "count": count,
        "patterns": [p["name"] for p in KNOWN_SCAM_PATTERNS],
    }


# ═══════════════════════════════════════════════════════════════════
# STATS
# ═══════════════════════════════════════════════════════════════════


async def ingest_forensic_report(
    report_text: str,
    report_name: str = "forensic_report",
    metadata: dict | None = None,
) -> dict:
    """
    Ingest a forensic report into the RAG knowledge base.
    Forensic reports are stored permanently (TTL=0) in the forensic_reports collection.
    """
    meta = {
        **(metadata or {}),
        "name": report_name,
        "type": "forensic_report",
    }
    return await ingest_document(
        collection="forensic_reports",
        content=report_text,
        metadata=meta,
    )


async def get_stats() -> dict[str, Any]:
    embedder = await get_embedder()
    r = await _get_redis()

    # Get ANN index stats FIRST - these have the authoritative vector counts
    ann_stats = {}
    try:
        from app.ann_index import get_ann_index  # external dependency

        ann = get_ann_index()
        ann_stats = ann.stats()
    except Exception:
        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    # Build collection sizes from ANN n_vectors (authoritative),
    # falling back to Redis SCARD only when ANN isn't built for that collection
    collection_sizes = {}
    for coll in COLLECTIONS:
        ann_entry = ann_stats.get(coll, {})
        if ann_entry.get("built") and ann_entry.get("n_vectors", 0) > 0:
            collection_sizes[coll] = ann_entry["n_vectors"]
        else:
            try:
                size = await r.scard(f"rag:idx:{coll}")
                collection_sizes[coll] = size
            except Exception:
                collection_sizes[coll] = 0

    # Get semantic cache stats
    cache_stats = {}
    try:
        from app.semantic_cache import get_semantic_cache  # external dependency

        cache = get_semantic_cache()
        cache_stats = await cache.stats()
    except Exception:
        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

    return {
        "embedder": embedder.stats,
        "collections": collection_sizes,
        "total_docs": sum(collection_sizes.values()),
        "seeded": _seeded,
        "ann_index": ann_stats,
        "semantic_cache": cache_stats,
    }


# ═══════════════════════════════════════════════════════════════════
# GOPLUS SECURITY ANALYSIS
# ═══════════════════════════════════════════════════════════════════


async def get_goplus_analysis(token_address: str, chain: str = "1") -> dict[str, Any]:
    """
    Run GoPlus Security API token risk check and return structured results.
    Chain IDs: 1=ETH, 56=BSC, 137=Polygon, 42161=Arbitrum, 43114=Avalanche,
    10=Optimism, 8453=Base, 324=zkSync, 534352=Scroll, 59144=Linea.
    """
    from app.scam_sources import GoPlusConnector  # external dependency

    result = await GoPlusConnector.check_token(chain, token_address)
    if not result:
        return {"error": "GoPlus check returned no data", "address": token_address, "chain": chain}

    # Ingest the result into the RAG knowledge base for future retrieval
    try:
        content = (
            f"GoPlus Security Scan [{chain}]: {token_address} - "
            f"honeypot={result.get('is_honeypot')}, "
            f"buy_tax={result.get('buy_tax')}, sell_tax={result.get('sell_tax')}, "
            f"proxy={result.get('is_proxy')}, "
            f"blacklisted={result.get('is_blacklisted')}, "
            f"hidden_owner={result.get('hidden_owner')}, "
            f"risk_score={result.get('risk_score')}"
        )
        await ingest_document(
            collection="token_analysis",
            content=content,
            metadata={
                "source": "goplus",
                "address": token_address,
                "chain": chain,
                **result,
            },
        )
    except Exception as e:
        logger.warning("Failed to ingest GoPlus result for %s: %s", token_address, e)

    return result

