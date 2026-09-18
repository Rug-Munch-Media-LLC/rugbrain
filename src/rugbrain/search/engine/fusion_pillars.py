"""
RAG Search Engine — RRF fusion of the three pillars.

Query-adaptive weighted reciprocal rank fusion with entity boost, optional
recency decay, and per-result pillar attribution.
"""

import logging
from typing import Any

from rugbrain.rrf_fusion import _rrf_fuse

from ..ranking import _classify_query

logger = logging.getLogger(__name__)


def _fuse_pillars(
    pillar1_results: list[dict[str, Any]],
    pillar2_results: list[dict[str, Any]],
    pillar3_results: list[dict[str, Any]],
    keyword_results: list[dict[str, Any]],
    query: str,
    hybrid: bool,
    entity_boost: float,
    use_recency_decay: bool,
    recency_half_life_days: float,
    recency_date_field: str,
) -> tuple[list[dict[str, Any]], bool, str, tuple[float, float, float], set[str]]:
    """RRF fusion with query-adaptive weights + pillar attribution."""
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

    # Keyword lane joins the fusion as a 4th ranked list when hybrid is on
    # (weighted like the sparse pillar — it is a lexical signal).
    fusion_lists: list[list[dict[str, Any]]] = [p1_ranked, p2_ranked, p3_ranked]
    fusion_weights: list[float] = [w_dense, w_sparse, w_entity]
    if hybrid:
        kw_ranked = sorted(
            keyword_results,
            key=lambda x: x.get("bm25_score", 0) or 0,
            reverse=True,
        )
        fusion_lists.append(kw_ranked)
        fusion_weights.append(w_sparse)

    entity_doc_ids = set()
    for r in pillar3_results:
        did = r.get("doc_id") or r.get("id")
        if did:
            entity_doc_ids.add(did)

    recency_applied = False
    if use_recency_decay:
        from ..fusion import rrf_fuse_recency

        fused = rrf_fuse_recency(
            fusion_lists,
            k=60,
            entity_boost=entity_boost,
            entity_doc_ids=entity_doc_ids,
            pillar_weights=fusion_weights,
            half_life_days=recency_half_life_days,
            date_field=recency_date_field,
        )
        recency_applied = any(r.get("recency_decay") is not None for r in fused)
        if recency_applied:
            logger.info(
                "Recency-aware fusion: half-life %s days (field=%s)",
                recency_half_life_days,
                recency_date_field,
            )
    else:
        fused = _rrf_fuse(
            fusion_lists,
            k=60,
            entity_boost=entity_boost,
            entity_doc_ids=entity_doc_ids,
            pillar_weights=fusion_weights,
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
        did_key = did if isinstance(did, str) else ""
        r["pillars"] = pillar_map.get(did_key, ["dense"])
        r["match_type"] = "+".join(r["pillars"])

    return fused, recency_applied, query_type, (w_dense, w_sparse, w_entity), entity_doc_ids
