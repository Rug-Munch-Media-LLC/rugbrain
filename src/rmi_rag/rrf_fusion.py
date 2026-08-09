from typing import Any


def _rrf_fuse(
    ranked_lists: list[list[dict[str, Any]]],
    k: int = 60,
    entity_boost: float = 1.5,
    entity_doc_ids: set[str] | None = None,
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
