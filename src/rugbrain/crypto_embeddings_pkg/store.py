"""Redis vector-store integration (store + search)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class StoreMixin:
    """Store embeddings in Redis and search by cosine similarity."""

    async def _get_redis(self) -> Any:
        raise NotImplementedError

    async def embed_query(self, query: str) -> list[float]:
        raise NotImplementedError

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        raise NotImplementedError

    async def store(
        self,
        collection: str,
        doc_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
        content: str = "",
    ) -> bool:
        """Store an embedding in Redis for fast retrieval."""
        try:
            r = await self._get_redis()
            key = f"rag:{collection}:{doc_id}"

            doc = {
                "id": doc_id,
                "vector": vector,
                "metadata": metadata or {},
                "content": content[:5000],
                "stored_at": datetime.now(UTC).isoformat(),
            }
            await r.setex(key, 86400 * 30, json.dumps(doc))  # 30 day TTL

            # Add to collection index
            await r.sadd(f"rag:idx:{collection}", doc_id)

            return True
        except Exception as e:
            logger.error("Failed to store embedding: %s", e)
            return False

    async def search(
        self,
        query: str,
        collection: str = "scam_patterns",
        limit: int = 10,
        min_similarity: float = 0.6,
    ) -> list[dict[str, Any]]:
        """
        Search for similar documents using cosine similarity.
        Compares query embedding against stored doc vectors (uses common prefix).
        """
        query_vec = await self.embed_query(query)
        q_dim = len(query_vec)

        try:
            r = await self._get_redis()
            doc_ids = await r.smembers(f"rag:idx:{collection}")

            if not doc_ids:
                return []

            # Batch fetch all documents
            keys = [f"rag:{collection}:{did}" for did in doc_ids]
            pipe = r.pipeline()
            for k in keys:
                pipe.get(k)
            results = await pipe.execute()

            # Compute similarities - compare only common prefix dimensions
            scored = []
            for _i, data in enumerate(results):
                if not data:
                    continue
                try:
                    doc = json.loads(data)
                except json.JSONDecodeError:
                    continue

                doc_vec = doc.get("vector", [])
                # Handle JSON-string vectors (from hash re-embed)
                if isinstance(doc_vec, str):
                    try:
                        doc_vec = json.loads(doc_vec)
                    except (json.JSONDecodeError, TypeError):
                        continue
                if not doc_vec or not isinstance(doc_vec, list):
                    continue

                # Compare only the semantic portion (query has semantic only, doc may be multi-head)
                compare_len = min(q_dim, len(doc_vec))
                sim = self.cosine_similarity(query_vec[:compare_len], doc_vec[:compare_len])
                if sim >= min_similarity:
                    scored.append(
                        {
                            "id": doc["id"],
                            "similarity": round(sim, 4),
                            "metadata": doc.get("metadata", {}),
                            "content": doc.get("content", "")[:500],
                        }
                    )

            scored.sort(key=lambda x: x["similarity"], reverse=True)
            return scored[:limit]

        except Exception as e:
            logger.error("Search failed: %s", e)
            return []
