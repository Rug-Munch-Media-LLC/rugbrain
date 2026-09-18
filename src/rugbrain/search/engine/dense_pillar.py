"""
RAG Search Engine — Pillar 1 dense vector lane.

Qdrant-backed multi-collection dense search with a standalone ANN/compat
fallback (semantic cache + per-variant ANN search + RRF fusion).
"""

import logging
from collections.abc import Callable
from typing import Any

from rugbrain.rrf_fusion import _rrf_fuse

from .vector_lane import _enrich_ann_results

logger = logging.getLogger(__name__)


async def _dense_pillar(
    query: str,
    collections: list[str],
    limit: int,
    min_similarity: float,
    content_limit: int,
    query_vec: list[float],
    query_variants: list[str],
    query_expansion_meta: dict[str, Any],
    expanded: bool,
    hyde: bool,
    multi_query: bool,
    llm_fn: Callable[..., Any] | None,
    embedder: Any,
) -> list[dict[str, Any]]:
    """Pillar 1: dense vector search (Qdrant-backed, ANN fallback)."""
    from rugbrain.search import engine as _e

    pillar1_results: list[dict[str, Any]] = []
    try:
        # Primary: the full-featured multi-collection dense search (Qdrant
        # vector + optional hybrid lanes; handles hyde/multi-query). This is
        # the same implementation the search endpoints use. The standalone
        # ANN/compat path below is only a fallback when Qdrant-backed search
        # is unavailable (ImportError, connection failure, etc.).
        pillar1_results = await _e.search_multi_collection(
            query,
            collections,
            limit=limit * 2,
            min_similarity=min_similarity,
            hyde=hyde,
            multi_query=multi_query,
            llm_fn=llm_fn,
        )
        for r in pillar1_results:
            r["pillar"] = "dense"
    except Exception as e:
        logger.warning("Pillar 1 (dense) primary failed: %s — trying ANN fallback", e)
        try:
            from rugbrain.compat import get_semantic_cache  # external dependency

            cache = get_semantic_cache()
            cached = await cache.check(query_vec)
            if cached is not None:
                pillar1_results = cached
                for r in pillar1_results:
                    r["pillar"] = "dense (cached)"
            else:
                # Pre-embed the variant vectors once (shared across collections).
                # The original query reuses ``query_vec``; the HyDE document
                # reuses its expansion embedding when available.
                if expanded:
                    hyde_doc = query_expansion_meta.get("hyde_doc")
                    hyde_vec = query_expansion_meta.get("hyde_embedding")
                    variant_vecs: list[tuple[str, list[float]]] = [(query, query_vec)]
                    for v in query_variants[1:]:
                        if hyde_doc is not None and v == hyde_doc and hyde_vec is not None:
                            variant_vecs.append((v, hyde_vec))
                        else:
                            variant_vecs.append((v, await embedder.embed_query(v)))
                else:
                    variant_vecs = [(query, query_vec)]

                for coll in collections:
                    try:
                        from rugbrain.compat import get_ann_index  # external dependency

                        ann = get_ann_index()
                        if expanded:
                            # Search each variant against the collection and
                            # RRF-fuse the per-variant dense lists.
                            variant_lists: list[list[dict[str, Any]]] = []
                            for _v, vec in variant_vecs:
                                ann_hits = await ann.search(
                                    query_embedding=vec,
                                    collection=coll,
                                    limit=limit,
                                    min_similarity=min_similarity,
                                )
                                enriched = await _enrich_ann_results(
                                    coll, ann_hits, content_limit=content_limit
                                )
                                if enriched:
                                    variant_lists.append(enriched)
                            if variant_lists:
                                ranked = [
                                    sorted(
                                        vl,
                                        key=lambda x: float(x.get("similarity", 0.0) or 0.0),
                                        reverse=True,
                                    )
                                    for vl in variant_lists
                                ]
                                fused_coll = _rrf_fuse(ranked, k=60)[: limit * 3]
                                for r in fused_coll:
                                    r["pillar"] = "dense"
                                pillar1_results.extend(fused_coll)
                        else:
                            ann_hits = await ann.search(
                                query_embedding=query_vec,
                                collection=coll,
                                limit=limit,
                                min_similarity=min_similarity,
                            )
                            enriched = await _enrich_ann_results(
                                coll, ann_hits, content_limit=content_limit
                            )
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
        except Exception:
            logger.warning("swallowed exception", exc_info=True)
    return pillar1_results
