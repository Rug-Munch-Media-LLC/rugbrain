import json
import logging
from typing import Any

from rugbrain.search_service import _get_redis

logger = logging.getLogger(__name__)


async def _sparse_text_search(
    query: str,
    collections: list[str],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Simple keyword-based text search over Redis-stored documents.
    Tokenizes the query and matches against content fields.
    This is a lightweight BM25-style fallback when pgvector FTS is not available.
    """
    r = await _get_redis()

    # Tokenize query
    query_tokens = set(query.lower().split())
    if not query_tokens:
        return []

    results = []
    for coll in collections:
        try:
            doc_ids = list(await r.smembers(f"rag:idx:{coll}"))
            if not doc_ids:
                continue

            # Batch fetch (limit to avoid massive reads)
            sample_ids = doc_ids[:200]
            keys = [f"rag:{coll}:{did}" for did in sample_ids]
            pipe = r.pipeline()
            for k in keys:
                pipe.get(k)
            raw_docs = await pipe.execute()

            for data in raw_docs:
                if not data:
                    continue
                try:
                    doc = json.loads(data)
                except json.JSONDecodeError:
                    continue

                content = doc.get("content", "").lower()
                if not content:
                    continue

                # BM25-style scoring: count matching tokens
                content_tokens = set(content.split())
                matching = query_tokens & content_tokens
                if not matching:
                    continue

                # Simple BM25-ish score: match ratio * idf-like factor
                score = len(matching) / (len(query_tokens) + 0.5)
                # Boost exact phrase matches
                if query.lower() in content:
                    score *= 2.0

                results.append(
                    {
                        "id": doc.get("id", ""),
                        "doc_id": doc.get("id", ""),
                        "text_score": round(score, 4),
                        "content": doc.get("content", "")[:500],
                        "metadata": doc.get("metadata", {}),
                        "collection": coll,
                    }
                )
        except Exception as e:
            logger.debug("Sparse search for %s failed: %s", coll, e)

    results.sort(key=lambda x: x["text_score"], reverse=True)
    return results[:limit]
