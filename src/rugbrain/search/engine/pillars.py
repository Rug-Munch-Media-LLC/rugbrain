"""
RAG Search Engine — three-pillar retrieval lanes.

Pillar 1 dense vector search (Qdrant-backed with ANN fallback), Pillar 2
sparse text search (SPLADE/BM25), Pillar 3 entity exact-match + Knowledge
Graph expansion, the optional hybrid keyword lane, and RRF fusion with
query-adaptive weights.
"""

import json
import logging
from typing import Any

from rugbrain.sparse_search import _sparse_text_search

from .constants import _HYBRID_KEYWORD_LIMIT_FACTOR

logger = logging.getLogger(__name__)


async def _fetch_parent_context(collection: str, parent_id: str) -> dict[str, Any] | None:
    """Fetch a parent window's stored doc for *parent_id*.

    Tries the chunk-keyed layout first (``rag:{collection}:{parent_id}`` —
    written by the parent-child ingest seam in ``search.filters``), then the
    parent-retriever layout (``rag:parent:{parent_id}``). Returns a dict with
    ``content`` / ``metadata``, or ``None`` when neither key exists.
    """
    from rugbrain.search import engine as _e

    r = await _e._get_redis()
    try:
        data = await r.get(f"rag:{collection}:{parent_id}")
        if data:
            doc = json.loads(data)
            return {"content": doc.get("content", ""), "metadata": doc.get("metadata", {})}
    except Exception:
        logger.warning("swallowed exception", exc_info=True)
    try:
        data = await r.get(f"rag:parent:{parent_id}")
        if data:
            doc = json.loads(data)
            return {"content": doc.get("content", ""), "metadata": doc.get("metadata", {})}
    except Exception:
        logger.warning("swallowed exception", exc_info=True)
    return None


async def _sparse_pillar(
    query: str,
    collections: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Pillar 2: sparse text search (BM25 + SPLADE)."""
    pillar2_results: list[dict[str, Any]] = []
    try:
        from rugbrain.splade_bm25 import splade_search

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
    return pillar2_results


async def _entity_pillar(
    query: str,
    use_kg: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pillar 3: entity exact-match + Knowledge Graph expansion."""
    pillar3_results: list[dict[str, Any]] = []
    entity_extraction: list[dict[str, Any]] = []
    try:
        from rugbrain.compat import EntityLookup, extract_entities  # external dependency

        entity_extraction = extract_entities(query)
        if entity_extraction:
            lookup = EntityLookup()
            for entity in entity_extraction:
                try:
                    matches = await lookup.lookup(entity["entity"])
                    for m in matches:
                        m["pillar"] = "entity"
                    pillar3_results.extend(matches)
                except Exception as e:
                    logger.debug("Entity lookup for %s failed: %s", entity["entity"], e)

            if use_kg:
                # Legacy Redis/Neo4j knowledge-graph lane (app.knowledge_graph)
                # is retired in favor of the Qdrant-native GraphRAG
                # (docs/graphrag-architecture.md). Guard the import so a
                # removed module degrades to ``kg = None`` instead of raising.
                kg = None
                try:
                    from rugbrain.compat import get_knowledge_graph  # external dependency

                    kg = await get_knowledge_graph()
                except ImportError:
                    logger.debug("Knowledge Graph module not available, skipping KG expansion")
                    kg = None
                except Exception as e:
                    logger.debug("KG access failed (non-critical): %s", e)
                    kg = None

                if kg is not None:
                    try:
                        kg_results = await kg.expand_query_entities(entity_extraction, max_depth=2)
                        if kg_results:
                            for r in kg_results:
                                r["pillar"] = "entity+kg"
                            pillar3_results.extend(kg_results)
                            logger.info("KG expansion: %s related entities", len(kg_results))
                    except Exception as e:
                        logger.debug("KG expansion failed (non-critical): %s", e)
    except Exception as e:
        logger.warning("Pillar 3 (entity) failed: %s", e)
    return pillar3_results, entity_extraction


async def _keyword_pillar(
    query: str,
    collections: list[str],
    limit: int,
    hybrid: bool,
) -> list[dict[str, Any]]:
    """Keyword/BM25 lane (hybrid, optional additive signal)."""
    keyword_results: list[dict[str, Any]] = []
    if hybrid:
        from rugbrain.search import engine as _e

        try:
            keyword_results = await _e._hybrid_keyword_search(
                query, collections, limit=limit * _HYBRID_KEYWORD_LIMIT_FACTOR
            )
            for r in keyword_results:
                r["pillar"] = "sparse"
            if keyword_results:
                logger.info("Hybrid keyword lane: %s hits", len(keyword_results))
        except Exception as e:
            logger.warning("Hybrid keyword lane failed (non-critical): %s", e)
    return keyword_results
