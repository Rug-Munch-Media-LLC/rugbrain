"""
RAG Search Engine — keyword/BM25 lane and RRF fusion.

The keyword lane reuses the existing stdlib-only Okapi BM25 core
(``rugbrain.splade_bm25.BM25Index``) — no heavy deps, no duplication.
A fresh in-memory index is built per query over chunk texts read from the
same Redis keys the vector lane enriches from, so doc ids match exactly
when RRF-fusing the two lanes.
"""

import asyncio
import json
import logging
from typing import Any

from rugbrain.rrf_fusion import _rrf_fuse
from rugbrain.search.lane_targets import redis_content_collection
from rugbrain.splade_bm25 import BM25Index

from .constants import (
    _HYBRID_CONTENT_PREVIEW,
    _HYBRID_KEYWORD_MAX_DOCS,
)

logger = logging.getLogger(__name__)


async def _collect_keyword_corpus(
    r: Any,
    collections: list[str],
    max_docs_per_collection: int,
) -> list[tuple[str, str, str, dict[str, Any]]]:
    """Read chunk docs from Redis for the BM25 lane."""
    corpus: list[tuple[str, str, str, dict[str, Any]]] = []
    for coll in collections:
        # Redis content keys are NOT lane-scoped: the ingest path writes
        # `rag:{base}:{id}` and the resolver only renames the Qdrant collection.
        # Search callers now hand us RESOLVED (`*_8b`) names, so strip the lane
        # suffix back off before touching Redis. Skipping this makes the whole
        # keyword lane return zero docs — a silent degradation, not an error.
        key_coll = redis_content_collection(coll) or coll
        try:
            doc_ids = list(await r.smembers(f"rag:idx:{key_coll}"))
        except Exception as e:
            logger.debug("hybrid: doc index read failed for %s: %s", coll, e)
            continue
        if not doc_ids:
            continue
        sample = doc_ids[:max_docs_per_collection]
        keys = [f"rag:{key_coll}:{did}" for did in sample]
        try:
            pipe = r.pipeline()
            for k in keys:
                pipe.get(k)
            raw_docs = await pipe.execute()
        except Exception as e:
            logger.debug("hybrid: redis fetch failed for %s: %s", coll, e)
            continue
        for did, data in zip(sample, raw_docs, strict=False):
            if not data:
                continue
            try:
                doc = json.loads(data)
            except json.JSONDecodeError:
                logger.debug("keyword_lane: unparseable doc json for %s", did)
                continue
            content = doc.get("content", "") or ""
            if not content.strip():
                continue
            corpus.append((str(did), coll, content, doc.get("metadata") or {}))
    return corpus


async def _hybrid_keyword_search(
    query: str,
    collections: list[str],
    limit: int = 20,
    max_docs_per_collection: int = _HYBRID_KEYWORD_MAX_DOCS,
) -> list[dict[str, Any]]:
    """Run the lightweight keyword/BM25 lane for *query* over *collections*.

    Reads chunk docs from Redis (``rag:{collection}:{doc_id}``), builds a
    fresh in-memory Okapi BM25 index, and returns ranked hits with
    ``bm25_score``. Doc ids are un-prefixed so RRF fusion with the vector
    lane (which uses plain doc ids) merges correctly. Returns ``[]`` when
    the corpus is empty or the lane fails — callers degrade to pure vector.
    """
    from rugbrain.search import engine as _e

    r = await _e._get_redis()
    # corpus rows: (doc_id, collection, content, metadata)
    corpus = await _collect_keyword_corpus(r, collections, max_docs_per_collection)
    if not corpus:
        return []

    def _bm25_rank(
        rows: list[tuple[str, str, str, dict[str, Any]]],
        q: str,
        n: int,
    ) -> list[dict[str, Any]]:
        index = BM25Index(k1=1.2, b=0.75)
        for did, _coll, content, metadata in rows:
            index.add_document(did, content, metadata)
        return index.search(q, limit=n)

    try:
        # Index build + scoring are CPU work — keep the event loop free.
        hits = await asyncio.to_thread(_bm25_rank, corpus, query, limit)
    except Exception as e:
        logger.debug("hybrid: BM25 build/search failed: %s", e)
        return []

    by_id = {did: (coll, content, metadata) for did, coll, content, metadata in corpus}
    results: list[dict[str, Any]] = []
    for hit in hits:
        did = str(hit.get("id", ""))
        coll, content, metadata = by_id.get(did, ("unknown", "", {}))
        results.append(
            {
                "id": did,
                "doc_id": did,
                "bm25_score": float(hit.get("bm25_score", 0.0)),
                "collection": coll,
                "metadata": metadata,
                "content": content[:_HYBRID_CONTENT_PREVIEW],
                "hybrid_source": "bm25",
            }
        )
    return results


def _hybrid_fuse(
    vector_lists: list[list[dict[str, Any]]],
    keyword_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fuse per-collection vector results with the keyword lane via RRF.

    Each list is ranked (vector lists by ``similarity``, keyword hits by
    ``bm25_score``) and merged with reciprocal rank fusion
    (``rugbrain.rrf_fusion._rrf_fuse``). Keyword-only hits surface in the
    fused output alongside vector hits; duplicates are merged on doc id.
    """
    ranked_lists: list[list[dict[str, Any]]] = [
        sorted(vlist, key=lambda x: float(x.get("similarity", 0.0) or 0.0), reverse=True)
        for vlist in vector_lists
    ]
    ranked_lists.append(
        sorted(keyword_results, key=lambda x: float(x.get("bm25_score", 0.0) or 0.0), reverse=True)
    )
    return _rrf_fuse(ranked_lists, k=60)
