"""
RAG Search Engine — result enrichment stages.

Rating boosts (Ground News-style quality reweighting), confidence scoring
and pre-generation reflection applied to the final result list.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _apply_rating_boosts(
    final_results: list[dict[str, Any]],
    use_rating_boosts: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Ground News-style quality reweighting from ``rating`` metadata."""
    rating_boosts_applied = False
    if use_rating_boosts and final_results:
        try:
            from rugbrain.ratings import apply_rating_boosts

            final_results = apply_rating_boosts(final_results)
            rating_boosts_applied = any(r.get("rating_boosted") for r in final_results)
        except ImportError:
            logger.debug("rugbrain.ratings not available — skipping rating boosts")
        except Exception as e:
            logger.debug("Rating boost failed (non-critical): %s", e)
    return final_results, rating_boosts_applied


def _score_confidence(
    query: str,
    final_results: list[dict[str, Any]],
    entity_doc_ids: set[str],
) -> Any:
    """Confidence scoring (standalone scorer, compat fallback)."""
    confidence = None
    try:
        # Try our new rugbrain confidence scorer first (standalone)
        from rugbrain.confidence_scorer import score_confidence

        entity_list = list(entity_doc_ids)[:5] if entity_doc_ids else []
        sources = []
        for r in final_results:
            src = {"type": r.get("type", "unknown"), "score": r.get("score", 0.0)}
            if r.get("metadata", {}).get("ingested_at"):
                try:
                    from datetime import datetime

                    src["timestamp"] = datetime.fromisoformat(
                        r["metadata"]["ingested_at"].replace("Z", "+00:00")
                    ).timestamp()
                except (ValueError, TypeError):
                    pass
            sources.append(src)
        confidence = score_confidence(
            sources,
            query_similarity=final_results[0].get("score", 0.0) if final_results else 0.0,
        )
    except ImportError:
        # Fallback to external rmi-backend confidence module
        try:
            from rugbrain.compat import score_confidence as score_confidence_compat

            entity_list = list(entity_doc_ids)[:5] if entity_doc_ids else []
            confidence = score_confidence_compat(
                final_results, query=query, entity_matches=entity_list
            )
        except ImportError:
            logger.debug("Confidence scoring not available (both modules missing)")
    except Exception as e:
        logger.debug("Confidence scoring failed (non-critical): %s", e)
    return confidence


async def _apply_reflection(query: str, final_results: list[dict[str, Any]]) -> Any:
    """Pre-generation reflection: verify on-chain refs before output."""
    reflection = None
    try:
        from rugbrain.pre_generation_reflection import pre_generation_reflection

        reflection = await pre_generation_reflection(query, final_results)
    except Exception as e:
        logger.debug("Pre-generation reflection failed (non-critical): %s", e)
    return reflection


async def _apply_parent_child(
    fused: list[dict[str, Any]],
    limit: int,
    use_parent_child: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Parent-child context expansion (chunk-keyed + ParentRetriever)."""
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
                        from rugbrain.search import engine as _e

                        coll = r.get("collection", "wallet_profiles")
                        pdoc = await _e._fetch_parent_context(coll, parent_id)
                        if pdoc:
                            r["parent_content"] = (pdoc.get("content") or "")[:2000]
                            r["parent_id"] = parent_id
                            r["parent_metadata"] = pdoc.get("metadata") or {}
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
        from rugbrain.parent_retriever import get_parent_retriever

        pr = await get_parent_retriever()
        if pr is not None:
            expanded = await pr.expand_context(fused)
            if expanded:
                fused = expanded
                parent_child_applied = True
    except ImportError:
        logger.debug("ParentRetriever module not available")
    except Exception as e:
        logger.debug("ParentRetriever.expand_context failed (non-critical): %s", e)
    return fused, parent_child_applied
