"""
RAG Search Engine — three-pillar hybrid search.

Pillar 1 dense vector, Pillar 2 sparse text, Pillar 3 entity + Knowledge
Graph; then RRF fusion, MMR dedup, cross-encoder reranking, parent-child
context expansion, rating boosts and confidence scoring.

Search-quality params (``use_reranker``/``rerank``/``use_mmr`` default ON,
each degrades gracefully when its backend is unavailable):

* ``hyde`` — HyDE hypothetical doc embedded as an extra dense vector.
* ``multi_query`` — query rewritten into 3-4 variations, each searched.
* ``synonym_expand`` — curated crypto-term aliases added as variants (OFF).
* ``llm_fn`` — optional LLM callable for expansion (sync or async).
* ``use_recency_decay`` — half-life decay folded into the RRF weight.
* ``hybrid`` — lightweight keyword/BM25 lane fused as an extra RRF signal.
* ``mmr_lambda`` — MMR diversity lambda (1.0 relevance, 0.0 diversity; 0.6).
* ``rerank`` — search-lane cross-encoder rerank of the top ``rerank_top_k``
  candidates, keeping ``rerank_keep`` (defaults to *limit*).
* ``rerank_timeout`` — seconds before the search-lane reranker falls back.
* ``use_reranker`` — news-lane cross-encoder reranker after fusion."""

import logging
from collections.abc import Callable
from typing import Any

from .constants import (
    _CODE_CONTENT_LIMIT,
    _MMR_LAMBDA_DEFAULT,
    _RERANK_DEFAULT,
    _RERANK_TIMEOUT,
    _SYNONYM_EXPAND_DEFAULT,
)
from .dense_pillar import DensePillarRequest, _dense_pillar
from .fusion_pillars import FusionRequest, _fuse_pillars
from .pillars import (
    _entity_pillar,
    _keyword_pillar,
    _sparse_pillar,
)
from .postprocess import PostprocessOptions, postprocess_results
from .query_expansion import ExpansionRequest, _build_search_variants
from .query_intent import detect_coding_query, resolve_search_collections

logger = logging.getLogger(__name__)


async def three_pillar_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.5,
    entity_boost: float = 1.5,
    use_mmr: bool = True,
    mmr_lambda: float = _MMR_LAMBDA_DEFAULT,
    use_kg: bool = True,
    use_parent_child: bool = True,
    use_reranker: bool = True,
    use_rating_boosts: bool = True,
    hybrid: bool = False,
    rerank: bool = _RERANK_DEFAULT,
    rerank_top_k: int = 20,
    rerank_keep: int | None = None,
    rerank_timeout: float = _RERANK_TIMEOUT,
    hyde: bool = False,
    multi_query: bool = False,
    synonym_expand: bool = _SYNONYM_EXPAND_DEFAULT,
    llm_fn: Callable[..., Any] | None = None,
    use_recency_decay: bool = False,
    recency_half_life_days: float = 30.0,
    recency_date_field: str = "published",
) -> dict[str, Any]:
    """Three-pillar hybrid search with KG expansion, MMR dedup, parent-child.

    Pillar 1 dense vector, Pillar 2 sparse (SPLADE+BM25), Pillar 3 entity +
    Knowledge Graph; then RRF fusion, MMR dedup, cross-encoder reranking,
    parent-child context expansion and rating boosts. Every optional stage
    degrades gracefully when its backend is unavailable.

    See the module docstring for the full parameter reference.
    """
    collections = resolve_search_collections(collections, query)

    limit = min(limit, 100)

    # Code-intent queries get longer per-result content (fenced code blocks
    # must not be truncated mid-block) and a code-chunk bias at the end.
    is_code_query = detect_coding_query(query)
    content_limit = _CODE_CONTENT_LIMIT if is_code_query else 500

    from rugbrain.search import engine as _e

    embedder = await _e.get_embedder()
    query_vec = await embedder.embed_query(query)

    # Query expansion (HyDE + multi-query + synonyms) — additive, default OFF.
    # Variants are built once and pre-embedded so the dense pillar can search
    # each.
    query_variants, query_expansion_meta = await _build_search_variants(
        ExpansionRequest(
            query=query,
            hyde=hyde,
            multi_query=multi_query,
            embedder=embedder,
            llm_fn=llm_fn,
            synonym_expand=synonym_expand,
        )
    )
    expanded = query_expansion_meta["variants"] > 1

    # ── Pillar 1: Dense vector search (ANN) ──
    pillar1_results = await _dense_pillar(
        DensePillarRequest(
            query=query,
            collections=collections,
            limit=limit,
            min_similarity=min_similarity,
            content_limit=content_limit,
            query_vec=query_vec,
            query_variants=query_variants,
            query_expansion_meta=query_expansion_meta,
            expanded=expanded,
            hyde=hyde,
            multi_query=multi_query,
            llm_fn=llm_fn,
            embedder=embedder,
        )
    )

    # ── Pillar 2: Sparse text search (BM25 + SPLADE) ──
    pillar2_results = await _sparse_pillar(query, collections, limit)

    # ── Pillar 3: Entity exact-match + Knowledge Graph expansion ──
    pillar3_results, entity_extraction = await _entity_pillar(query, use_kg)

    # ── Keyword/BM25 lane (hybrid, optional additive signal) ──
    keyword_results = await _keyword_pillar(query, collections, limit, hybrid)

    # ── RRF fusion with query-adaptive weights ──
    fused, recency_applied, query_type, weights, entity_doc_ids = _fuse_pillars(
        FusionRequest(
            pillar1_results=pillar1_results,
            pillar2_results=pillar2_results,
            pillar3_results=pillar3_results,
            keyword_results=keyword_results,
            query=query,
            hybrid=hybrid,
            entity_boost=entity_boost,
            use_recency_decay=use_recency_decay,
            recency_half_life_days=recency_half_life_days,
            recency_date_field=recency_date_field,
        )
    )
    w_dense, w_sparse, w_entity = weights

    # ── Post-fusion stages (MMR, rerank, parent-child, ratings, confidence) ──
    post = await postprocess_results(
        query,
        fused,
        limit,
        PostprocessOptions(
            use_mmr=use_mmr,
            mmr_lambda=mmr_lambda,
            use_reranker=use_reranker,
            use_parent_child=use_parent_child,
            use_rating_boosts=use_rating_boosts,
            rerank=rerank,
            rerank_top_k=rerank_top_k,
            rerank_keep=rerank_keep,
            rerank_timeout=rerank_timeout,
            entity_doc_ids=entity_doc_ids,
        ),
    )

    return {
        "results": post["results"],
        "reflection": post["reflection"],
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
            "mmr_applied": post["mmr_applied"],
            "mmr_lambda": mmr_lambda if post["mmr_applied"] else None,
            "pre_mmr_count": post["pre_mmr_count"],
            "post_mmr_count": post["post_mmr_count"],
            "kg_expansion": len([r for r in pillar3_results if r.get("pillar") == "entity+kg"]),
            "parent_child_applied": post["parent_child_applied"],
            "reranker_applied": post["reranker_applied"],
            "reranker_method": post["reranker_method"],
            "rating_boosts_applied": post["rating_boosts_applied"],
            "hybrid_keyword_hits": len(keyword_results),
            "search_reranker_applied": post["search_reranker_applied"],
            "search_reranker_method": post["search_reranker_method"],
            "query_expansion": query_expansion_meta,
            "recency_applied": recency_applied,
            "recency_half_life_days": recency_half_life_days if recency_applied else None,
        },
        "entity_extraction": entity_extraction if entity_extraction else None,
        "query": query,
        "query_type": query_type,
        "fusion_weights": {"dense": w_dense, "sparse": w_sparse, "entity": w_entity},
        "collections": collections,
        "confidence": post["confidence"],
    }
