"""
RAG Search — ingestion, filter/query building, and stats.

Extracted from search_service.py.
Handles document ingestion, embedding routing, scam pattern pre-embedding,
GoPlus security analysis, and system statistics.
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any, cast

from rugbrain.crypto_embeddings import (
    KNOWN_SCAM_PATTERNS,
    CryptoEmbedder,
    EmbeddingResult,
    get_embedder,
)

from ._state import (  # noqa: F401  (_pattern_cache_loaded bound here, written via global)
    _get_redis,
    _pattern_cache,
    _pattern_cache_loaded,
    _seeded,
)

logger = logging.getLogger(__name__)

# Per-collection Redis TTLs (seconds). Shared by the single-doc ingest and
# the parent-child ingest seam (parent windows live as long as their docs).
_COLLECTION_TTL_MAP = {
    "scam_patterns": 0,
    "contract_audits": 0,
    "transaction_patterns": 0,
    "forensic_reports": 0,
    "wallet_profiles": 86400 * 365,
    "known_scams": 86400 * 365,
    "market_intel": 86400 * 365,
    "news_articles": 86400 * 30,
    "token_analysis": 86400 * 90,
    "general": 86400 * 30,
}


async def _preembed_scam_patterns() -> None:
    """
    Pre-embed all KNOWN_SCAM_PATTERNS and cache results.
    Loads from Redis first (persistent cache), then embeds any missing ones.
    """
    global _pattern_cache, _pattern_cache_loaded

    embedder = await get_embedder()
    r = await _get_redis()

    for pattern in KNOWN_SCAM_PATTERNS:
        pattern_name = pattern["name"]

        if pattern_name in _pattern_cache:
            continue

        redis_key = f"rag:pattern_cache:{pattern_name}"
        try:
            cached_data = await r.get(redis_key)
            if cached_data:
                cached = json.loads(cached_data)
                _pattern_cache[pattern_name] = EmbeddingResult(
                    vector=cached["vector"],
                    dims=cached["dims"],
                    model=cached["model"],
                    head=cached.get("head", "scam_pattern"),
                )
                logger.debug("Pattern cache hit (Redis): %s", pattern_name)
                continue
        except Exception as e:
            logger.debug("Redis pattern cache read failed for %s: %s", pattern_name, e)

        try:
            result = await embedder.embed_scam_pattern(
                pattern_name=pattern["name"],
                description=pattern["description"],
                code_snippets=pattern.get("code_snippets", []),
                indicators=pattern.get("indicators", []),
                severity=pattern.get("severity", "high"),
            )
            _pattern_cache[pattern_name] = result

            try:
                await r.setex(
                    redis_key,
                    86400 * 30,
                    json.dumps(
                        {
                            "vector": result.vector,
                            "dims": result.dims,
                            "model": result.model,
                            "head": result.head,
                        }
                    ),
                )
            except Exception as e:
                logger.debug("Redis pattern cache write failed for %s: %s", pattern_name, e)

            logger.info("Pre-embedded scam pattern: %s", pattern_name)
        except Exception as e:
            logger.warning("Failed to pre-embed pattern %s: %s", pattern_name, e)

    _pattern_cache_loaded = True


# ═══════════════════════════════════════════════════════════════════
# EMBEDDING ROUTER
# ═══════════════════════════════════════════════════════════════════


async def _embed_by_collection(
    embedder: CryptoEmbedder,
    collection: str,
    content: str,
    metadata: dict[str, Any],
) -> EmbeddingResult:
    """Route to the right embedding head based on collection type."""
    if collection in ("scam_patterns", "known_scams"):
        return await embedder.embed_scam_pattern(
            pattern_name=metadata.get("name", content[:50]),
            description=content,
            severity=metadata.get("severity", "medium"),
        )
    elif collection == "token_analysis":
        code = metadata.get("contract_code", "")
        return await embedder.embed_token_scam(
            name=metadata.get("name", ""),
            symbol=metadata.get("symbol", ""),
            description=content,
            contract_code=code,
            chain=metadata.get("chain", "solana"),
            metadata=metadata,
        )
    elif collection == "wallet_profiles":
        return await embedder.embed_wallet(
            address=metadata.get("address", ""),
            labels=metadata.get("labels", []),
            transactions=metadata.get("transactions", []),
            chain=metadata.get("chain", "solana"),
            balance_usd=float(metadata.get("balance_usd", 0) or 0),
        )
    elif collection == "contract_audits":
        vec = await embedder._semantic_embed_one(content, "semantic")
        return EmbeddingResult(
            vector=vec,
            dims=len(vec),
            model="qwen3-embedding-4b",
            head="contract_audit",
        )
    else:
        vec = await embedder._semantic_embed_one(content, "semantic")
        return EmbeddingResult(
            vector=vec,
            dims=len(vec),
            model="openai/text-embedding-3-large",
            head="semantic",
        )


# ═══════════════════════════════════════════════════════════════════
# CORE INGEST
# ═══════════════════════════════════════════════════════════════════


async def ingest_document(
    collection: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    doc_id: str | None = None,
    *,
    parent_child: bool = False,
    parent_token_target: int = 3000,
    parent_max_chars: int = 16000,
) -> dict[str, Any]:
    """
    Ingest a document with real semantic embedding.

    Additive ``parent_child`` switch: when True, *content* is split into
    small search-oriented child chunks that reference larger parent context
    windows (see ``ingest_document_parent_child``); the standard single-doc
    behavior is preserved for all other callers (default False).
    """
    if parent_child:
        return await ingest_document_parent_child(
            collection,
            content,
            metadata,
            doc_id,
            parent_token_target=parent_token_target,
            parent_max_chars=parent_max_chars,
        )

    embedder = await get_embedder()
    metadata = metadata or {}

    if not doc_id:
        doc_id = hashlib.sha256(
            f"{collection}:{content[:100]}:{datetime.now(UTC).isoformat()}".encode()
        ).hexdigest()[:16]

    result = await _embed_by_collection(embedder, collection, content, metadata)

    r = await _get_redis()
    doc = {
        "id": doc_id,
        "collection": collection,
        "vector": result.vector,
        "dims": result.dims,
        "model": result.model,
        "metadata": {**metadata, "head": result.head},
        "content": content[:5000],
        "stored_at": datetime.now(UTC).isoformat(),
    }
    ttl = _COLLECTION_TTL_MAP.get(collection, 86400 * 30)
    key = f"rag:{collection}:{doc_id}"
    if ttl == 0:
        await r.set(key, json.dumps(doc))
    else:
        await r.setex(key, ttl, json.dumps(doc))
    await r.sadd(f"rag:idx:{collection}", doc_id)

    try:
        from rugbrain.compat import get_ann_index, AnnIndex  # external dependency

        ann = get_ann_index()
        await ann.bump_version(collection)  # type: ignore[operator]
    except Exception as e:
        logger.debug("ANN version bump skipped: %s", e)

    try:
        from rugbrain.splade_bm25 import _bm25_built_at, _bm25_index  # external dependency

        if _bm25_index is not None:
            _bm25_built_at = 0
            logger.debug("BM25 index cache invalidated for incremental rebuild")
    except Exception as e:
        logger.debug("BM25 cache invalidation skipped: %s", e)

    try:
        from rugbrain.compat import EntityLookup, extract_entities  # external dependency

        entities = extract_entities(content)
        if entities.has_entities:  # type: ignore[union-attr]
            lookup = EntityLookup.get_instance()  # type: ignore[union-attr]
            for entity in entities.all_entities():  # type: ignore[union-attr]
                await lookup.index_entity(
                    entity["type"],
                    entity["value"],
                    doc_id,
                    metadata={**metadata, "collection": collection},
                )
            logger.debug("Entity indexing: %s entities", len(entities.all_entities()))  # type: ignore[union-attr]
    except Exception as e:
        logger.debug("Entity indexing skipped: %s", e)

    kg = None
    try:
        from rugbrain.compat import get_knowledge_graph  # external dependency

        kg = await get_knowledge_graph()  # type: ignore[operator]
    except ImportError:
        logger.debug("Knowledge Graph module not available, skipping KG edge creation")
        kg = None
    except Exception as e:
        logger.debug("KG access failed (non-critical): %s", e)
        kg = None

    if kg is not None:
        try:
            await kg.ingest_rag_document(collection, doc_id, content, metadata)  # type: ignore[operator]
            logger.debug("KG edge creation complete")
        except Exception as e:
            logger.debug("KG edge creation failed: %s", e)

    logger.debug("pgvector upsert skipped (FAISS primary)")

    try:
        from rugbrain.compat import get_parent_retriever  # external dependency

        pr = await get_parent_retriever()  # type: ignore[operator]
        await pr.store_parent(doc_id=doc_id, content=content, metadata=metadata)  # type: ignore[union-attr]
    except Exception as e:
        logger.debug("ParentRetriever.store_parent skipped: %s", e)

    logger.info("Ingested %s/%s: %s...", collection, doc_id, content[:60])
    return {"id": doc_id, "dims": result.dims, "collection": collection}


async def ingest_forensic_report(
    report_text: str,
    report_name: str = "forensic_report",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Ingest a forensic report into the RAG knowledge base.
    Forensic reports are stored permanently (TTL=0) in the forensic_reports collection.
    """
    meta = {
        **(metadata or {}),
        "name": report_name,
        "type": "forensic_report",
    }
    return await ingest_document(
        collection="forensic_reports",
        content=report_text,
        metadata=meta,
    )


async def ingest_document_parent_child(
    collection: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    doc_id: str | None = None,
    *,
    parent_token_target: int = 3000,
    parent_max_chars: int = 16000,
) -> dict[str, Any]:
    """
    Parent-child ingest: split *content* into child chunks referencing parents.

    Wires ``rugbrain.app_chunking.split_doc_parent_child`` into the ingest
    seam so children carry ``parent_id`` / ``parent_index`` / ``parent_total``
    metadata, and parent windows are stored for retrieval-time context
    expansion. The retrieval side (``search.engine``) already expands a
    matched child to its parent — this makes the stored parents reachable:

      * ``rag:{collection}:{parent_id}`` — chunk-keyed layout read by the
        engine's inline parent-child expansion;
      * ``rag:parent:{parent_id}`` — parent-retriever layout read by
        ``ParentRetriever.get_parent`` / ``expand_context``.

    Additive: the standard single-doc ``ingest_document`` is unchanged. Each
    child is ingested through that same path (embed + store + index); parent
    windows are context-only docs (no embedding, not added to the searchable
    index) so they never surface as standalone hits.

    Returns a summary dict: ``{id, collection, parent_child, parent_count,
    child_count, child_ids}``. Falls back to a single-doc ingest when the
    input cannot be split into parent windows.
    """
    metadata = metadata or {}
    from rugbrain.app_chunking import split_doc_parent_child

    parents = split_doc_parent_child(
        content,
        content_type="default",
        doc_meta=metadata,
        parent_token_target=parent_token_target,
        parent_max_chars=parent_max_chars,
    )
    if not parents:
        # Un-splittable input (blank / single short section) — standard path.
        result = await ingest_document(collection, content, metadata, doc_id)
        result["parent_child"] = True
        result["parent_count"] = 0
        result["child_count"] = 1
        return result

    if not doc_id:
        doc_id = hashlib.sha256(
            f"{collection}:{content[:100]}:{datetime.now(UTC).isoformat()}".encode()
        ).hexdigest()[:16]

    r = await _get_redis()
    ttl = _COLLECTION_TTL_MAP.get(collection, 86400 * 30)
    now_iso = datetime.now(UTC).isoformat()

    child_ids: list[str] = []
    for parent in parents:
        pid = parent.metadata["parent_id"]
        parent_meta = {**metadata, **parent.metadata}
        parent_doc = {
            "id": pid,
            "collection": collection,
            "content": parent.content,
            "metadata": {**parent_meta, "is_parent": True},
            "stored_at": now_iso,
            "model": "",
            "dims": 0,
        }
        # Layout 1 — chunk-keyed parent (read by engine's inline expansion).
        key = f"rag:{collection}:{pid}"
        if ttl == 0:
            await r.set(key, json.dumps(parent_doc))
        else:
            await r.setex(key, ttl, json.dumps(parent_doc))
        # Layout 2 — parent-retriever store (get_parent / expand_context).
        try:
            from rugbrain.parent_retriever import get_parent_retriever

            pr = await get_parent_retriever()
            await pr.store_parent(doc_id=pid, content=parent.content, metadata=parent_meta)
        except Exception as e:
            logger.debug("ParentRetriever.store_parent skipped for %s: %s", pid, e)

        for child in parent.child_chunks:
            child_text = str(child.get("text", "") or "")
            if not child_text.strip():
                continue
            child_meta = {**metadata, **{k: v for k, v in child.items() if k != "text"}}
            child_result = await ingest_document(collection, child_text, child_meta)
            child_ids.append(child_result["id"])

    logger.info(
        "Ingested %s/%s parent-child: %s parents, %s children",
        collection,
        doc_id,
        len(parents),
        len(child_ids),
    )
    return {
        "id": doc_id,
        "collection": collection,
        "parent_child": True,
        "parent_count": len(parents),
        "child_count": len(child_ids),
        "child_ids": child_ids,
    }


# ═══════════════════════════════════════════════════════════════════
# STATS
# ═══════════════════════════════════════════════════════════════════


async def get_stats() -> dict[str, Any]:
    embedder = await get_embedder()
    r = await _get_redis()

    ann_stats = {}
    try:
        from rugbrain.compat import get_ann_index, AnnIndex  # external dependency

        ann = get_ann_index()
        ann_stats = ann.stats()  # type: ignore[operator]
    except Exception:
        logger.warning("swallowed exception", exc_info=True)

    from rugbrain.crypto_embeddings import COLLECTIONS

    collection_sizes = {}
    for coll in COLLECTIONS:
        ann_entry = ann_stats.get(coll, {})
        if ann_entry.get("built") and ann_entry.get("n_vectors", 0) > 0:
            collection_sizes[coll] = ann_entry["n_vectors"]
        else:
            try:
                size = await r.scard(f"rag:idx:{coll}")
                collection_sizes[coll] = size
            except Exception:
                collection_sizes[coll] = 0

    cache_stats = {}
    try:
        from rugbrain.compat import get_semantic_cache, SemanticCache  # external dependency

        cache = get_semantic_cache()
        cache_stats = await cache.stats()  # type: ignore[operator]
    except Exception:
        logger.warning("swallowed exception", exc_info=True)

    return {
        "embedder": embedder.stats,
        "collections": collection_sizes,
        "total_docs": sum(collection_sizes.values()),
        "seeded": _seeded,
        "ann_index": ann_stats,
        "semantic_cache": cache_stats,
    }


# ═══════════════════════════════════════════════════════════════════
# GOPLUS SECURITY ANALYSIS
# ═══════════════════════════════════════════════════════════════════


async def get_goplus_analysis(token_address: str, chain: str = "1") -> dict[str, Any]:
    """
    Run GoPlus Security API token risk check and return structured results.
    Chain IDs: 1=ETH, 56=BSC, 137=Polygon, 42161=Arbitrum, 43114=Avalanche,
    10=Optimism, 8453=Base, 324=zkSync, 534352=Scroll, 59144=Linea.
    """
    from rugbrain.compat import GoPlusConnector  # external dependency

    result = await GoPlusConnector.check_token(chain, token_address)  # type: ignore[operator]
    if not result:
        return {"error": "GoPlus check returned no data", "address": token_address, "chain": chain}

    try:
        content = (
            f"GoPlus Security Scan [{chain}]: {token_address} - "
            f"honeypot={result.get('is_honeypot')}, "
            f"buy_tax={result.get('buy_tax')}, sell_tax={result.get('sell_tax')}, "
            f"proxy={result.get('is_proxy')}, "
            f"blacklisted={result.get('is_blacklisted')}, "
            f"hidden_owner={result.get('hidden_owner')}, "
            f"risk_score={result.get('risk_score')}"
        )
        await ingest_document(
            collection="token_analysis",
            content=content,
            metadata={
                "source": "goplus",
                "address": token_address,
                "chain": chain,
                **result,
            },
        )
    except Exception as e:
        logger.warning("Failed to ingest GoPlus result for %s: %s", token_address, e)

    return cast(dict[str, Any], result)
