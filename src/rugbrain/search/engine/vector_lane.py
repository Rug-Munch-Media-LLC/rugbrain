"""
RAG Search Engine — dense vector lane.

Single-collection semantic search (FAISS ANN with brute-force fallback),
ANN result enrichment from Redis, and the semantic-cache read/write path.
"""

import json
import logging
import time
from typing import Any, cast

logger = logging.getLogger(__name__)


async def search_similar(
    query: str,
    collection: str = "wallet_profiles",
    limit: int = 10,
    min_similarity: float = 0.0,
    content_limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Semantic search across any collection.
    Uses FAISS ANN index for fast retrieval, with semantic cache.

    ``content_limit`` caps per-result content (see ``_enrich_ann_results``).
    """
    t0 = time.time()

    from rugbrain.search import engine as _e

    embedder = await _e.get_embedder()
    query_vec = await embedder.embed_query(query)

    # 1. Check semantic cache
    cache_hit = False
    try:
        from rugbrain.compat import get_semantic_cache  # external dependency

        cache = get_semantic_cache()
        cached = await cache.check(query_vec)
        if cached is not None:
            cache_hit = True
            logger.info("Semantic cache hit for query: %s", query[:60])
            try:
                from rugbrain.observability import trace_retrieval  # external dependency

                trace_retrieval((time.time() - t0) * 1000, collection, len(cached), cache_hit=True)
            except Exception:
                logger.warning("swallowed exception", exc_info=True)
            return cast(list[dict[str, Any]], cached)
    except Exception as e:
        logger.debug("Semantic cache check skipped: %s", e)

    # 2. Try FAISS ANN index search
    results = []
    try:
        from rugbrain.compat import get_ann_index  # external dependency

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
                    await _enrich_ann_results(collection, needs_enrich, content_limit=content_limit)
                    if needs_enrich
                    else []
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
            from rugbrain.compat import get_semantic_cache  # external dependency

            cache = get_semantic_cache()
            await cache.store(query_vec, results)
        except Exception as e:
            logger.debug("Semantic cache store skipped: %s", e)

    # Apply temporal decay weighting (boost recent content)
    if results:
        try:
            from rugbrain.search.temporal_decay import apply_temporal_decay

            results = apply_temporal_decay(results)
        except Exception as exc:
            logger.debug("temporal decay skipped: %s", exc)

    # Trace observability
    try:
        from rugbrain.observability import trace_retrieval  # external dependency

        trace_retrieval((time.time() - t0) * 1000, collection, len(results), cache_hit=cache_hit)
    except Exception:
        logger.warning("swallowed exception", exc_info=True)

    return results


async def _enrich_ann_results(
    collection: str,
    ann_results: list[dict[str, Any]],
    content_limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Given ANN results (id + similarity), fetch full documents from Redis
    and merge the content/metadata.

    ``content_limit`` caps how much content is returned per result; code-intent
    queries pass a larger cap so fenced code blocks are not truncated mid-block.
    """
    if not ann_results:
        return []

    from rugbrain.search import engine as _e

    r = await _e._get_redis()
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
                    "content": doc.get("content", "")[:content_limit],
                    "metadata": doc.get("metadata", {}),
                    "model": doc.get("model", ""),
                    "collection": doc.get("collection", collection),
                }
            )
        except json.JSONDecodeError:
            logger.debug("vector_lane: unparseable doc json for %s", ar.get("id"))
            enriched.append(ar)

    return enriched
