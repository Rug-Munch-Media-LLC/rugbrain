"""
RAG Search Engine — dense, sparse, entity, hybrid search.

Three-pillar hybrid search (dense + sparse + entity) with:
  - Knowledge Graph expansion (Pillar 3+)
  - MMR deduplication after RRF fusion
  - Parent-child retrieval for contextual chunks
  - SPLADE+BM25 sparse search (Pillar 2)
  - Query-adaptive fusion weights

Extracted from search_service.py.
All cross-module deps resolved via search._state / inline imports.
"""

import asyncio
import json
import logging
from typing import Any

from rmi_rag.crypto_embeddings import COLLECTIONS, get_embedder
from rmi_rag.rrf_fusion import _rrf_fuse
from rmi_rag.sparse_search import _sparse_text_search

from ._state import _get_redis
from .ranking import _classify_query

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# CORE SEARCH
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
            try:
                from rmi_rag.observability import trace_retrieval  # external dependency

                trace_retrieval((time.time() - t0) * 1000, collection, len(cached), cache_hit=True)
            except Exception:
                logger.warning("swallowed exception", exc_info=True)
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
                needs_enrich = [r for r in ann_results if "content" not in r]
                enriched = (
                    await _enrich_ann_results(collection, needs_enrich) if needs_enrich else []
                )
                results = [r for r in ann_results if "content" in r] + enriched
        else:
            logger.info("FAISS not built for %s, using brute-force fallback", collection)
            results = await embedder.search(
                query=query,
                collection=collection,
                limit=limit,
                min_similarity=min_similarity,
            )
    except Exception as e:
        logger.warning("ANN/pgvector search failed, falling back to brute-force: %s", e)
        results = await embedder.search(
            query=query,
            collection=collection,
            limit=limit,
            min_similarity=min_similarity,
        )

    # 3. Store in semantic cache
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
        logger.warning("swallowed exception", exc_info=True)

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

    Returns dict with merged results + pillar attribution + MMR metadata.
    """
    if collections is None:
        collections = COLLECTIONS

    limit = min(limit, 100)

    embedder = await get_embedder()
    query_vec = await embedder.embed_query(query)

    # ── Pillar 1: Dense vector search (ANN) ──
    pillar1_results: list[dict[str, Any]] = []
    try:
        from app.semantic_cache import get_semantic_cache  # external dependency

        cache = get_semantic_cache()
        cached = await cache.check(query_vec)
        if cached is not None:
            pillar1_results = cached
            for r in pillar1_results:
                r["pillar"] = "dense (cached)"
        else:
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
                    try:
                        hits = await embedder.search(
                            query, coll, limit=limit, min_similarity=min_similarity
                        )
                        for r in hits:
                            r["pillar"] = "dense"
                        pillar1_results.extend(hits)
                    except Exception:
                        logger.warning("swallowed exception", exc_info=True)

            if pillar1_results:
                await cache.store(query_vec, pillar1_results)
    except Exception as e:
        logger.warning("Pillar 1 (dense) failed: %s", e)
        try:
            pillar1_results = await search_multi_collection(
                query, collections, limit=limit * 2, min_similarity=min_similarity
            )
            for r in pillar1_results:
                r["pillar"] = "dense"
        except Exception:
            logger.warning("swallowed exception", exc_info=True)

    # ── Pillar 2: Sparse text search (BM25 + SPLADE) ──
    pillar2_results: list[dict[str, Any]] = []
    try:
        from rmi_rag.splade_bm25 import splade_search

        pillar2_results = await splade_search(query, collections, limit=limit * 2)
        for r in pillar2_results:
            r["pillar"] = "sparse"
    except ImportError:
        try:
            pillar2_results = await _sparse_text_search(query, collections, limit=limit * 2)
            for r in pillar2_results:
                r["pillar"] = "sparse"
        except Exception as e:
            logger.warning("Pillar 2 (sparse) fallback failed: %s", e)
    except Exception as e:
        logger.warning("Pillar 2 (SPLADE) failed: %s", e)
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
                    logger.debug("Entity lookup for %s failed: %s", entity["value"], e)

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
    query_type = _classify_query(query)
    weights = {
        "entity_heavy": (0.3, 0.2, 0.5),
        "keyword_heavy": (0.3, 0.6, 0.1),
        "semantic_heavy": (0.7, 0.2, 0.1),
        "balanced": (0.4, 0.4, 0.2),
    }
    w_dense, w_sparse, w_entity = weights.get(query_type, weights["balanced"])

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
                top_k=limit * 3,
            )
            mmr_applied = True
            logger.info(
                "MMR dedup: %s → %s results (lambda=%s)", pre_mmr_count, len(fused), mmr_lambda
            )
        except ImportError:
            logger.debug("MMR dedup module not available, skipping")
        except Exception as e:
            logger.debug("MMR dedup failed (non-critical): %s", e)

    # ── Cross-encoder reranking (optional) ──
    reranker_applied = False
    reranker_method: str | None = None
    if use_reranker and fused:
        for r in fused:
            r["similarity"] = r.get("rrf_score", 0.0)
        pre_rerank = len(fused)

        # Stage 1: Ollama qwen3-embed:4b cosine approximation
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
            expanded_results = []
            for r in fused[: limit * 2]:
                content = r.get("content", "") or ""
                metadata = r.get("metadata", {}) or {}

                parent_id = metadata.get("parent_id") or r.get("parent_id")
                if parent_id:
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
                        logger.warning("swallowed exception", exc_info=True)

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
