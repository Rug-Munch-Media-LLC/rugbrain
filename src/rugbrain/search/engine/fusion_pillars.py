"""
RAG Search Engine — RRF fusion of the three pillars.

Query-adaptive weighted reciprocal rank fusion with entity boost, optional
recency decay, and per-result pillar attribution.
"""

import logging
from dataclasses import dataclass
from typing import Any

from rugbrain.rrf_fusion import _rrf_fuse

from ..ranking import _classify_query

logger = logging.getLogger(__name__)


@dataclass
class FusionRequest:
    """Inputs for :func:`_fuse_pillars` (keeps function arity ≤5)."""

    pillar1_results: list[dict[str, Any]]
    pillar2_results: list[dict[str, Any]]
    pillar3_results: list[dict[str, Any]]
    keyword_results: list[dict[str, Any]]
    query: str
    hybrid: bool
    entity_boost: float
    use_recency_decay: bool
    recency_half_life_days: float
    recency_date_field: str


_ADAPTIVE_WEIGHTS: dict[str, tuple[float, float, float]] = {
    "entity_heavy": (0.3, 0.2, 0.5),
    "keyword_heavy": (0.3, 0.6, 0.1),
    "semantic_heavy": (0.7, 0.2, 0.1),
    "balanced": (0.4, 0.4, 0.2),
}


def _adaptive_weights(query: str) -> tuple[str, tuple[float, float, float]]:
    """Classify the query and return its fusion weight triple."""
    query_type = _classify_query(query)
    return query_type, _ADAPTIVE_WEIGHTS.get(query_type, _ADAPTIVE_WEIGHTS["balanced"])


def _fusion_inputs(
    req: FusionRequest,
    w_dense: float,
    w_sparse: float,
    w_entity: float,
) -> tuple[list[list[dict[str, Any]]], list[float]]:
    """Build the ranked fusion lists (keyword lane joins when hybrid is on)."""
    p1_ranked = sorted(req.pillar1_results, key=lambda x: x.get("similarity", 0), reverse=True)
    p2_ranked = sorted(
        req.pillar2_results,
        key=lambda x: x.get("text_score", 0) or x.get("sparse_score", 0) or x.get("bm25_score", 0),
        reverse=True,
    )
    p3_ranked = sorted(req.pillar3_results, key=lambda x: x.get("score", 0), reverse=True)
    fusion_lists: list[list[dict[str, Any]]] = [p1_ranked, p2_ranked, p3_ranked]
    fusion_weights: list[float] = [w_dense, w_sparse, w_entity]
    if req.hybrid:
        kw_ranked = sorted(
            req.keyword_results,
            key=lambda x: x.get("bm25_score", 0) or 0,
            reverse=True,
        )
        fusion_lists.append(kw_ranked)
        fusion_weights.append(w_sparse)
    return fusion_lists, fusion_weights


def _pillar_attribution(
    pillar1_results: list[dict[str, Any]],
    pillar2_results: list[dict[str, Any]],
    pillar3_results: list[dict[str, Any]],
    fused: list[dict[str, Any]],
) -> None:
    """Annotate each fused result with the pillars that produced it."""
    pillar_map: dict[str, list[str]] = {}
    for results, label in (
        (pillar1_results, "dense"),
        (pillar2_results, "sparse"),
        (pillar3_results, "entity"),
    ):
        for r in results:
            did = r.get("id") or r.get("doc_id")
            if did:
                pillar_map.setdefault(did, [])
                if label not in pillar_map[did]:
                    pillar_map[did].append(label)
    for r in fused:
        did = r.get("id") or r.get("doc_id")
        did_key = did if isinstance(did, str) else ""
        r["pillars"] = pillar_map.get(did_key, ["dense"])
        r["match_type"] = "+".join(r["pillars"])


def _fuse_pillars(req: FusionRequest) -> tuple[list[dict[str, Any]], bool, str, tuple[float, float, float], set[str]]:
    """RRF fusion with query-adaptive weights + pillar attribution."""
    query_type, (w_dense, w_sparse, w_entity) = _adaptive_weights(req.query)
    fusion_lists, fusion_weights = _fusion_inputs(req, w_dense, w_sparse, w_entity)

    entity_doc_ids = set()
    for r in req.pillar3_results:
        did = r.get("doc_id") or r.get("id")
        if did:
            entity_doc_ids.add(did)

    recency_applied = False
    if req.use_recency_decay:
        from ..fusion import rrf_fuse_recency

        fused = rrf_fuse_recency(
            fusion_lists,
            k=60,
            entity_boost=req.entity_boost,
            entity_doc_ids=entity_doc_ids,
            pillar_weights=fusion_weights,
            half_life_days=req.recency_half_life_days,
            date_field=req.recency_date_field,
        )
        recency_applied = any(r.get("recency_decay") is not None for r in fused)
        if recency_applied:
            logger.info(
                "Recency-aware fusion: half-life %s days (field=%s)",
                req.recency_half_life_days,
                req.recency_date_field,
            )
    else:
        fused = _rrf_fuse(
            fusion_lists,
            k=60,
            entity_boost=req.entity_boost,
            entity_doc_ids=entity_doc_ids,
            pillar_weights=fusion_weights,
        )

    _pillar_attribution(req.pillar1_results, req.pillar2_results, req.pillar3_results, fused)

    return fused, recency_applied, query_type, (w_dense, w_sparse, w_entity), entity_doc_ids
