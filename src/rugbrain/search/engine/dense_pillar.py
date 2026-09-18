"""
RAG Search Engine — Pillar 1 dense vector lane.

Qdrant-backed multi-collection dense search with a standalone ANN/compat
fallback (semantic cache + per-variant ANN search + RRF fusion).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rugbrain.rrf_fusion import _rrf_fuse

from .vector_lane import _enrich_ann_results

logger = logging.getLogger(__name__)


@dataclass
class DensePillarRequest:
    """Inputs for :func:`_dense_pillar` (keeps function arity ≤5)."""

    query: str
    collections: list[str]
    limit: int
    min_similarity: float
    content_limit: int
    query_vec: list[float]
    query_variants: list[str]
    query_expansion_meta: dict[str, Any]
    expanded: bool
    hyde: bool
    multi_query: bool
    llm_fn: Callable[..., Any] | None
    embedder: Any


async def _dense_pillar(req: DensePillarRequest) -> list[dict[str, Any]]:
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
            req.query,
            req.collections,
            limit=req.limit * 2,
            min_similarity=req.min_similarity,
            hyde=req.hyde,
            multi_query=req.multi_query,
            llm_fn=req.llm_fn,
        )
        for r in pillar1_results:
            r["pillar"] = "dense"
    except Exception as e:
        logger.warning("Pillar 1 (dense) primary failed: %s — trying ANN fallback", e)
        try:
            from rugbrain.compat import get_semantic_cache  # external dependency

            cache = get_semantic_cache()
            cached = await cache.check(req.query_vec)
            if cached is not None:
                pillar1_results = cached
                for r in pillar1_results:
                    r["pillar"] = "dense (cached)"
            else:
                # Pre-embed the variant vectors once (shared across collections).
                # The original query reuses ``query_vec``; the HyDE document
                # reuses its expansion embedding when available.
                variant_vecs = await _variant_vectors(req)

                pillar1_results.extend(await _ann_fallback(req, variant_vecs))

                if pillar1_results:
                    await cache.store(req.query_vec, pillar1_results)
        except Exception:
            logger.warning("swallowed exception", exc_info=True)
    return pillar1_results

async def _variant_vectors(req: DensePillarRequest) -> list[tuple[str, list[float]]]:
    """Pre-embed the query variants once (shared across collections)."""
    if not req.expanded:
        return [(req.query, req.query_vec)]
    hyde_doc = req.query_expansion_meta.get("hyde_doc")
    hyde_vec = req.query_expansion_meta.get("hyde_embedding")
    variant_vecs: list[tuple[str, list[float]]] = [(req.query, req.query_vec)]
    for v in req.query_variants[1:]:
        if hyde_doc is not None and v == hyde_doc and hyde_vec is not None:
            variant_vecs.append((v, hyde_vec))
        else:
            variant_vecs.append((v, await req.embedder.embed_query(v)))
    return variant_vecs


async def _ann_fallback(
    req: DensePillarRequest,
    variant_vecs: list[tuple[str, list[float]]],
) -> list[dict[str, Any]]:
    """Standalone ANN/compat search across the request's collections."""
    out: list[dict[str, Any]] = []
    for coll in req.collections:
        try:
            from rugbrain.compat import get_ann_index  # external dependency

            ann = get_ann_index()
            if req.expanded:
                out.extend(await _ann_search_expanded(req, ann, coll, variant_vecs))
            else:
                ann_hits = await ann.search(
                    query_embedding=req.query_vec,
                    collection=coll,
                    limit=req.limit,
                    min_similarity=req.min_similarity,
                )
                enriched = await _enrich_ann_results(coll, ann_hits, content_limit=req.content_limit)
                for r in enriched:
                    r["pillar"] = "dense"
                out.extend(enriched)
        except Exception as e:
            logger.debug("ANN search for %s failed: %s", coll, e)
            try:
                hits = await req.embedder.search(
                    req.query, coll, limit=req.limit, min_similarity=req.min_similarity
                )
                for r in hits:
                    r["pillar"] = "dense"
                out.extend(hits)
            except Exception:
                logger.warning("swallowed exception", exc_info=True)
    return out


async def _ann_search_expanded(
    req: DensePillarRequest,
    ann: Any,
    coll: str,
    variant_vecs: list[tuple[str, list[float]]],
) -> list[dict[str, Any]]:
    """Search each variant against one collection and RRF-fuse the lists."""
    variant_lists: list[list[dict[str, Any]]] = []
    for _v, vec in variant_vecs:
        ann_hits = await ann.search(
            query_embedding=vec,
            collection=coll,
            limit=req.limit,
            min_similarity=req.min_similarity,
        )
        enriched = await _enrich_ann_results(coll, ann_hits, content_limit=req.content_limit)
        if enriched:
            variant_lists.append(enriched)
    if not variant_lists:
        return []
    ranked = [
        sorted(vl, key=lambda x: float(x.get("similarity", 0.0) or 0.0), reverse=True)
        for vl in variant_lists
    ]
    fused_coll = _rrf_fuse(ranked, k=60)[: req.limit * 3]
    for r in fused_coll:
        r["pillar"] = "dense"
    return fused_coll
