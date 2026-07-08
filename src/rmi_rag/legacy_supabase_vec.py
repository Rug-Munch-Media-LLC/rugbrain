#!/usr/bin/env python3
# DEPRECATED — replaced by app/rag/qdrant_store.py (Qdrant vector store).
# This module is no longer imported by any code in the codebase.
# Retained for reference; remove after verifying Qdrant migration is stable.
"""
TIER-1 VECTOR STORE - Supabase pgvector
=======================================
Replaces brute-force Redis cosine search with proper ANN indexing.
Uses Supabase pgvector extension - IVFFlat/HNSW indexes.
Scales to millions of documents with sub-20ms queries.

Architecture:
  - pgvector table with IVFFlat index (fast approximate search)
  - Hybrid search: dense (vector) + sparse (BM25 text)
  - Metadata filtering (chain, severity, date range)
  - Automatic index maintenance
"""

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx
from dotenv import load_dotenv

# Load env vars at module level so keys are available before any class instantiation
load_dotenv("/app/.env", override=True)

logger = logging.getLogger(__name__)


def _get_url():
    """Get Supabase URL from env dynamically."""
    return os.environ.get("SUPABASE_URL", "")


def _get_headers():
    """Build headers dynamically to pick up env changes without module reload."""
    key = os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get(
        "SUPABASE_SERVICE_ROLE_KEY", ""
    )
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


# Table configuration
VECTOR_TABLE = "rag_vectors"

# Dynamic embedding dimension - determined by the active model.
# Local BGE-small = 384, BGE-M3/OpenRouter = 3072, etc.
# Set via env var or detect at runtime from the embedder.
EMBEDDING_DIM = int(os.environ.get("RAG_EMBEDDING_DIM", "0"))  # 0 = auto-detect

# The target dimension for the pgvector table column and RPC calls.
# This MUST match the vector(N) in the CREATE TABLE and search_embeddings RPC.
# Migration scripts use 640, .env sets RAG_EMBEDDING_DIM=640.
# Defaults to EMBEDDING_DIM if set, otherwise 640.
TABLE_DIM = (
    int(os.environ.get("RAG_TABLE_DIM", str(max(EMBEDDING_DIM, 1)))) if EMBEDDING_DIM > 0 else 640
)


def pad_vector(vec: list, target_dim: int) -> list:
    """Pad or truncate a vector to exactly target_dim dimensions."""
    if len(vec) == target_dim:
        return vec
    if len(vec) > target_dim:
        return vec[:target_dim]
    return vec + [0.0] * (target_dim - len(vec))


# SQL for table creation
CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {VECTOR_TABLE} (
    id TEXT PRIMARY KEY,
    collection TEXT NOT NULL,
    content TEXT,
    embedding vector({TABLE_DIM}),
    metadata JSONB DEFAULT '{{}}',
    source TEXT,
    severity TEXT,
    chain TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_rag_collection ON {VECTOR_TABLE} (collection);
CREATE INDEX IF NOT EXISTS idx_rag_metadata ON {VECTOR_TABLE} USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_rag_severity ON {VECTOR_TABLE} (severity);
CREATE INDEX IF NOT EXISTS idx_rag_chain ON {VECTOR_TABLE} (chain);
CREATE INDEX IF NOT EXISTS idx_rag_source ON {VECTOR_TABLE} (source);

-- Full-text search index for BM25-style keyword matching
CREATE INDEX IF NOT EXISTS idx_rag_content_fts ON {VECTOR_TABLE}
    USING GIN (to_tsvector('english', COALESCE(content, '')));

-- IVFFlat index for approximate nearest neighbor search
-- Note: Must be created after data is loaded for best results
-- CREATE INDEX IF NOT EXISTS idx_rag_embedding_ivfflat ON {VECTOR_TABLE}
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
"""


class SupabaseVectorStore:
    """
    Tier-1 vector store using Supabase pgvector.
    Supports:
      - ANN search with IVFFlat/HNSW
      - Hybrid search (dense + sparse)
      - Metadata filtering
      - Batch ingestion
      - Collection management
    """

    def __init__(self):
        self._table_ready = False
        self._resolved_dim = EMBEDDING_DIM  # may be 0 = auto-detect

    def _get_dim(self, embedding: list[float] | None = None) -> int:
        """Resolve the embedding dimension: env var > actual vector length > fallback 384."""
        if self._resolved_dim > 0:
            return self._resolved_dim
        # Auto-detect from the first embedding we see
        if embedding and len(embedding) > 0:
            self._resolved_dim = len(embedding)
            logger.info(f"Auto-detected embedding dimension: {self._resolved_dim}")
            return self._resolved_dim
        # Default: local BGE-small-en-v1.5 = 384
        self._resolved_dim = 384
        logger.info(f"Using default embedding dimension: {self._resolved_dim}")
        return self._resolved_dim

    async def _rpc(self, fn: str, params: dict | None = None) -> dict:
        """Execute a Supabase RPC function."""
        url = f"{_get_url()}/rest/v1/rpc/{fn}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=params or {}, headers=_get_headers())
            resp.raise_for_status()
            return resp.json() if resp.text else {}

    async def _query(
        self,
        table: str,
        select: str = "*",
        filters: dict | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Query Supabase REST API."""
        url = f"{_get_url()}/rest/v1/{table}"
        params = {"select": select, "limit": str(limit), "offset": str(offset)}
        if filters:
            for k, v in filters.items():
                params[k] = f"eq.{v}"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params, headers=_get_headers())
            resp.raise_for_status()
            return resp.json() if resp.text else []

    async def _upsert(self, table: str, rows: list[dict]) -> dict:
        """Upsert rows into a Supabase table."""
        url = f"{_get_url()}/rest/v1/{table}"
        params = {"on_conflict": "id"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=rows, params=params, headers=_get_headers())
            resp.raise_for_status()
            return resp.json() if resp.text else {}

    async def initialize(self) -> bool:
        """Create table and indexes if not exist. Falls back gracefully."""
        global TABLE_DIM
        # Determine the query dimension for the RPC health check.
        # If the RPC function already exists on Supabase, it has a fixed signature
        # (e.g. vector(640) or vector(1024)). We probe with TABLE_DIM first,
        # then fall back to common alternatives.
        rpc_probed = False
        for probe_dim in [TABLE_DIM, 640, 1024, 384]:
            try:
                await self._rpc(
                    "search_embeddings",
                    {
                        "query_embedding": [0.1] * probe_dim,
                        "match_count": 1,
                        "similarity_threshold": 0.0,
                    },
                )
                self._table_ready = True
                TABLE_DIM = probe_dim
                logger.info(f"pgvector: search_embeddings RPC available (dim={probe_dim})")
                rpc_probed = True
                break
            except Exception:
                logging.getLogger(__name__).warning("swallowed exception", exc_info=True)
                continue
        if not rpc_probed:
            logger.warning("pgvector: search_embeddings RPC not available")

        # Try to create table via SQL RPC if available
        if not self._table_ready:
            try:
                # Build the CREATE TABLE SQL with the resolved dimension
                dim = self._get_dim()
                create_sql = f"""
                CREATE TABLE IF NOT EXISTS {VECTOR_TABLE} (
                    id TEXT PRIMARY KEY,
                    collection TEXT NOT NULL,
                    content TEXT,
                    embedding vector({dim}),
                    metadata JSONB DEFAULT '{{}}',
                    source TEXT,
                    severity TEXT,
                    chain TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_rag_collection ON {VECTOR_TABLE} (collection);
                CREATE INDEX IF NOT EXISTS idx_rag_metadata ON {VECTOR_TABLE} USING GIN (metadata);
                CREATE INDEX IF NOT EXISTS idx_rag_content_fts ON {VECTOR_TABLE}
                    USING GIN (to_tsvector('english', COALESCE(content, '')));
                """
                await self._rpc("exec_sql", {"sql": create_sql})
                self._table_ready = True
            except Exception:
                logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

        # Try direct insert to check if table exists
        if not self._table_ready:
            try:
                health_dim = TABLE_DIM
                test_row = {
                    "id": "_pgvector_health_check",
                    "collection": "_system",
                    "embedding": [0.0] * health_dim,
                    "metadata": json.dumps({"health_check": True}),
                    "source": "system",
                    "severity": "info",
                }
                url = f"{_get_url()}/rest/v1/rag_vectors"
                params = {"on_conflict": "id"}
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        url, json=test_row, params=params, headers=_get_headers()
                    )
                    if resp.status_code in (200, 201):
                        self._table_ready = True
                        logger.info("pgvector: rag_vectors table ready")
                        # Cleanup test row
                        await client.delete(
                            f"{_get_url()}/rest/v1/rag_vectors?id=eq._pgvector_health_check",
                            headers=_get_headers(),
                        )
            except Exception as e:
                logger.warning(
                    "pgvector table not found. Run: /root/backend/supabase_pgvector_setup.sql in Supabase SQL Editor"
                )
                logger.warning(f"Falling back to Redis vector store. Error: {e}")

        if not self._table_ready:
            logger.warning("pgvector unavailable - using Redis fallback for vector search")
            self._table_ready = False  # Signal to use Redis fallback

        return self._table_ready

    async def insert(
        self,
        doc_id: str,
        collection: str,
        embedding: list[float],
        content: str = "",
        metadata: dict | None = None,
        source: str = "",
        severity: str = "medium",
        chain: str = "",
    ) -> bool:
        """Insert a single vector document. Falls back to Redis if pgvector unavailable."""
        if not self._table_ready:
            # Fallback to Redis
            try:
                from rmi_rag.search_service import _get_redis

                r = await _get_redis()
                doc = {
                    "id": doc_id,
                    "collection": collection,
                    "vector": embedding,
                    "dims": len(embedding),
                    "metadata": metadata or {},
                    "content": content[:5000],
                    "source": source,
                    "severity": severity,
                }
                await r.setex(f"rag:{collection}:{doc_id}", 86400 * 30, json.dumps(doc))
                await r.sadd(f"rag:idx:{collection}", doc_id)
                return True
            except Exception as e:
                logger.error(f"Redis fallback insert failed: {e}")
                return False

        try:
            row = {
                "id": doc_id,
                "collection": collection,
                "content": content[:10000],
                "embedding": pad_vector(embedding, TABLE_DIM),
                "metadata": json.dumps(metadata or {}),
                "source": source,
                "severity": severity,
                "chain": chain,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            await self._upsert(VECTOR_TABLE, [row])
            return True
        except Exception as e:
            logger.error(f"pgvector insert failed: {e}")
            return False

    async def insert_batch(self, docs: list[dict]) -> int:
        """Insert multiple documents in one batch (max 100 per call)."""
        count = 0
        for i in range(0, len(docs), 100):
            batch = docs[i : i + 100]
            rows = []
            for doc in batch:
                rows.append(
                    {
                        "id": doc["id"],
                        "collection": doc["collection"],
                        "content": doc.get("content", "")[:10000],
                        "embedding": pad_vector(doc["embedding"], TABLE_DIM),
                        "metadata": json.dumps(doc.get("metadata", {})),
                        "source": doc.get("source", ""),
                        "severity": doc.get("severity", "medium"),
                        "chain": doc.get("chain", ""),
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )
            try:
                await self._upsert(VECTOR_TABLE, rows)
                count += len(rows)
            except Exception as e:
                logger.error(f"Batch insert failed at batch {i}: {e}")
        return count

    async def search(
        self,
        query_embedding: list[float],
        collection: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.6,
        filters: dict | None = None,
    ) -> list[dict[str, Any]]:
        """
        ANN search using pgvector. Falls back to Redis.
        """
        # Fallback to Redis
        if not self._table_ready:
            try:
                from rmi_rag.crypto_embeddings import get_embedder
                from rmi_rag.search_service import _get_redis

                r = await _get_redis()
                doc_ids = await r.smembers(f"rag:idx:{collection or 'known_scams'}")
                if not doc_ids:
                    return []
                keys = [f"rag:{collection or 'known_scams'}:{did}" for did in doc_ids]
                pipe = r.pipeline()
                for k in keys:
                    pipe.get(k)
                results = await pipe.execute()
                embedder = await get_embedder()  # Use singleton, not CryptoEmbedder()
                q_len = len(query_embedding)
                scored = []
                for data in results:
                    if not data:
                        continue
                    try:
                        doc = json.loads(data)
                    except Exception:
                        logging.getLogger(__name__).warning("swallowed exception", exc_info=True)
                        continue
                    dv = doc.get("vector", [])
                    if not dv:
                        continue
                    compare_len = min(q_len, len(dv))
                    sim = embedder.cosine_similarity(
                        query_embedding[:compare_len], dv[:compare_len]
                    )
                    if sim >= min_similarity:
                        scored.append(
                            {
                                "id": doc["id"],
                                "similarity": round(sim, 4),
                                "content": doc.get("content", ""),
                                "metadata": doc.get("metadata", {}),
                                "source": doc.get("source", ""),
                                "severity": doc.get("severity", ""),
                            }
                        )
                scored.sort(key=lambda x: x["similarity"], reverse=True)
                return scored[:limit]
            except Exception as e:
                logger.error(f"Redis search fallback failed: {e}")
                return []

        # pgvector search (primary)
        # Pad query vector to the table dimension before calling RPC
        padded_query = pad_vector(query_embedding, TABLE_DIM)
        try:
            results = await self._rpc(
                "search_embeddings",
                {
                    "query_embedding": padded_query,
                    "namespace": collection or "default",
                    "match_count": limit,
                    "similarity_threshold": min_similarity,
                },
            )
            # search_embeddings returns [] for no matches - that's a valid result
            if results is not None:
                return [
                    {
                        "id": r.get("id") or r.get("document_id"),
                        "similarity": round(r.get("similarity", 0), 4),
                        "content": r.get("content", ""),
                        "metadata": r.get("metadata", {}),
                        "source": r.get("source", ""),
                        "severity": r.get("severity", ""),
                    }
                    for r in (results or [])
                ]
        except Exception as e:
            logger.warning(f"search_embeddings RPC failed: {e}")

        # Fallback: direct SQL query via REST
        # Build filter query
        filter_params = {}
        if collection:
            filter_params["collection"] = f"eq.{collection}"
        if filters:
            for k, v in filters.items():
                filter_params[k] = f"eq.{v}"

        # Construct the query with vector similarity
        # Using cosine distance: 1 - (embedding <=> query)
        # Pad or truncate to match the table column dimension
        embedding_padded = pad_vector(query_embedding, TABLE_DIM)

        # Format embedding as pgvector literal - safe since it's all floats
        embedding_literal = "[" + ",".join(f"{x:.8f}" for x in embedding_padded) + "]"

        # Escape single quotes in collection name to prevent SQL injection
        safe_collection = collection.replace("'", "''") if collection else ""
        safe_min_sim = max(0.0, min(1.0, float(min_similarity)))  # clamp to valid range

        try:
            # Try to use a raw SQL query via RPC
            collection_filter = f"AND collection = '{safe_collection}'" if safe_collection else ""
            sql = f"""
            SELECT id, collection, content, metadata, source, severity, chain,
                   1 - (embedding <=> '{embedding_literal}'::vector) AS similarity
            FROM {VECTOR_TABLE}
            WHERE 1 - (embedding <=> '{embedding_literal}'::vector) > {safe_min_sim}
            {collection_filter}
            ORDER BY embedding <=> '{embedding_literal}'::vector
            LIMIT {int(limit)}
            """  # noqa: S608 - inputs sanitized: embedding_literal is a float list, safe_min_sim/limit are clamped ints, safe_collection is single-quote-escaped
            results = await self._rpc("exec_sql_returning", {"sql": sql})
            if results:
                return [
                    {
                        "id": r["id"],
                        "similarity": round(r["similarity"], 4),
                        "content": r.get("content", ""),
                        "metadata": r.get("metadata", {}),
                        "source": r.get("source", ""),
                        "severity": r.get("severity", ""),
                    }
                    for r in results
                ]
        except Exception as e:
            logger.warning(f"Direct SQL vector search failed: {e}")

        return []

    async def hybrid_search(
        self,
        query_text: str,
        query_embedding: list[float],
        collection: str | None = None,
        limit: int = 10,
        vector_weight: float = 0.7,
    ) -> list[dict[str, Any]]:
        """
        Hybrid search: dense vector + sparse text (BM25 via tsvector).
        Combines semantic similarity with exact keyword matching.
        Critical for: finding exact code snippets, function names, error messages.
        """
        # Get vector results
        vector_results = await self.search(query_embedding, collection=collection, limit=limit * 2)

        # Get text search results
        text_results = []
        try:
            # Escape user input for PostgreSQL text search - prevent SQL injection
            safe_query_text = query_text.replace("'", "''").replace("\\", "\\\\")
            safe_collection = collection.replace("'", "''") if collection else ""
            collection_filter = f"AND collection = '{safe_collection}'" if safe_collection else ""
            safe_limit = int(limit * 2)

            sql = f"""
            SELECT id, collection, content, metadata, source, severity,
                   ts_rank(to_tsvector('english', COALESCE(content, '')),
                           plainto_tsquery('english', '{safe_query_text}')) AS text_score
            FROM {VECTOR_TABLE}
            WHERE to_tsvector('english', COALESCE(content, '')) @@ plainto_tsquery('english', '{safe_query_text}')
            {collection_filter}
            ORDER BY text_score DESC
            LIMIT {safe_limit}
            """  # noqa: S608 - safe_query_text/safe_collection are quote-escaped, safe_limit is int-cast
            text_results_raw = await self._rpc("exec_sql_returning", {"sql": sql})
            if text_results_raw:
                max_score = max(r["text_score"] for r in text_results_raw) or 1.0
                text_results = [
                    {
                        "id": r["id"],
                        "similarity": round(r["text_score"] / max_score, 4),
                        "content": r.get("content", ""),
                        "metadata": r.get("metadata", {}),
                        "source": r.get("source", ""),
                        "severity": r.get("severity", ""),
                    }
                    for r in text_results_raw
                ]
        except Exception as e:
            logger.warning(f"Text search failed: {e}")

        # Merge with weighted Reciprocal Rank Fusion
        merged = {}
        for rank, r in enumerate(vector_results):
            merged[r["id"]] = {
                **r,
                "rrf_score": vector_weight / (60 + rank + 1),
                "match_type": "vector",
            }
        for rank, r in enumerate(text_results):
            rrf = (1 - vector_weight) / (60 + rank + 1)
            if r["id"] in merged:
                merged[r["id"]]["rrf_score"] += rrf
                merged[r["id"]]["match_type"] = "hybrid"
            else:
                merged[r["id"]] = {
                    **r,
                    "rrf_score": rrf,
                    "match_type": "text",
                }

        # Sort and return top results
        sorted_results = sorted(merged.values(), key=lambda x: x["rrf_score"], reverse=True)
        final = []
        for r in sorted_results[:limit]:
            r.pop("rrf_score", None)
            final.append(r)

        return final

    async def get_collection_stats(self) -> dict[str, int]:
        """Get document counts per collection."""
        try:
            sql = f"""
            SELECT collection, COUNT(*) as count
            FROM {VECTOR_TABLE}
            GROUP BY collection
            ORDER BY count DESC
            """  # noqa: S608 - VECTOR_TABLE is a hardcoded constant
            rows = await self._rpc("exec_sql_returning", {"sql": sql})
            return {r["collection"]: r["count"] for r in (rows or [])}
        except Exception:
            return {}

    async def delete_collection(self, collection: str) -> int:
        """Delete all documents in a collection."""
        safe_collection = collection.replace("'", "''")
        try:
            sql = f"DELETE FROM {VECTOR_TABLE} WHERE collection = '{safe_collection}'"  # noqa: S608 - safe_collection is quote-escaped
            result = await self._rpc("exec_sql", {"sql": sql})
            if isinstance(result, list) and result:
                return result[0].get("count", 1) if isinstance(result[0], dict) else 1
            return 1
        except Exception:
            return 0

    async def delete_by_id(self, doc_id: str) -> bool:
        """Delete a single document."""
        safe_id = doc_id.replace("'", "''")
        try:
            sql = f"DELETE FROM {VECTOR_TABLE} WHERE id = '{safe_id}'"  # noqa: S608 - safe_id is quote-escaped
            await self._rpc("exec_sql", {"sql": sql})
            return True
        except Exception:
            return False

    async def total_docs(self) -> int:
        """Total document count."""
        try:
            sql = f"SELECT COUNT(*) as count FROM {VECTOR_TABLE}"  # noqa: S608 - VECTOR_TABLE is a hardcoded constant
            rows = await self._rpc("exec_sql_returning", {"sql": sql})
            return rows[0]["count"] if rows else 0
        except Exception:
            return 0

    async def build_hnsw_index(
        self,
        m: int = 16,
        ef_construction: int = 200,
    ) -> bool:
        """
        Build/rebuild HNSW index for ANN search.
        HNSW provides better recall than IVFFlat at similar query speeds,
        and does not require a training step.

        Parameters:
          m:               Max connections per layer (default 16; higher = more recall, more memory)
          ef_construction: Build-time search width (default 200; higher = better index quality)
        """
        try:
            # Drop existing HNSW index if present (to rebuild with new params)
            try:
                drop_sql = "DROP INDEX IF EXISTS idx_rag_embedding_hnsw;"
                await self._rpc("exec_sql", {"sql": drop_sql})
            except Exception:
                logging.getLogger(__name__).warning("swallowed exception", exc_info=True)

            sql = f"""
            CREATE INDEX IF NOT EXISTS idx_rag_embedding_hnsw
            ON {VECTOR_TABLE} USING hnsw (embedding vector_cosine_ops)
            WITH (m = {int(m)}, ef_construction = {int(ef_construction)});
            """
            await self._rpc("exec_sql", {"sql": sql})
            logger.info(f"HNSW index built (m={m}, ef_construction={ef_construction})")
            return True
        except Exception as e:
            logger.warning(f"HNSW index build failed: {e}")
            return False

    async def build_index(self) -> bool:
        """
        Build/rebuild ANN index for fast vector search.
        Prefers HNSW (higher recall, no training) over IVFFlat.
        Falls back to IVFFlat if HNSW fails.
        """
        # Try HNSW first (better quality, no training step)
        hnsw_ok = await self.build_hnsw_index(m=16, ef_construction=200)
        if hnsw_ok:
            return True

        # Fallback: IVFFlat
        try:
            sql = f"""
            CREATE INDEX IF NOT EXISTS idx_rag_embedding_ivfflat
            ON {VECTOR_TABLE} USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100);
            """
            await self._rpc("exec_sql", {"sql": sql})
            logger.info("IVFFlat index built (HNSW fallback)")
            return True
        except Exception as e:
            logger.warning(f"Index build failed (both HNSW and IVFFlat): {e}")
            return False

    async def list_distinct(self, column: str, collection: str | None = None) -> list[str]:
        """Get distinct values for a column."""
        # Whitelist allowed columns to prevent SQL injection
        allowed_columns = {"source", "severity", "chain", "collection"}
        safe_column = column if column in allowed_columns else "source"
        try:
            where = ""
            if collection:
                safe_collection = collection.replace("'", "''")
                where = f"WHERE collection = '{safe_collection}'"
            sql = (
                f"SELECT DISTINCT {safe_column} FROM {VECTOR_TABLE} {where} ORDER BY {safe_column}"  # noqa: S608 - safe_column validated against whitelist, safe_collection quote-escaped
            )
            rows = await self._rpc("exec_sql_returning", {"sql": sql})
            return [r[safe_column] for r in (rows or [])]
        except Exception:
            return []


# ══════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════

_vector_store: SupabaseVectorStore | None = None


async def get_vector_store() -> SupabaseVectorStore:
    global _vector_store
    # Re-init only if not ready (preserves working singleton across calls)
    if _vector_store is None or not _vector_store._table_ready:
        _vector_store = SupabaseVectorStore()
        await _vector_store.initialize()
    return _vector_store
