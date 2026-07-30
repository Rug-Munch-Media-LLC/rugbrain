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
from typing import Any

from rmi_rag.crypto_embeddings import (
    KNOWN_SCAM_PATTERNS,
    CryptoEmbedder,
    EmbeddingResult,
    get_embedder,
)
from ._state import _get_redis, _pattern_cache, _pattern_cache_loaded, _seeded

logger = logging.getLogger(__name__)


async def _preembed_scam_patterns():
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
    metadata: dict,
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
            model="local/bge-small-en-v1.5",
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
    metadata: dict | None = None,
    doc_id: str | None = None,
) -> dict:
    """
    Ingest a document with real semantic embedding.
    """
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
    _TTL_MAP = {
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
    ttl = _TTL_MAP.get(collection, 86400 * 30)
    key = f"rag:{collection}:{doc_id}"
    if ttl == 0:
        await r.set(key, json.dumps(doc))
    else:
        await r.setex(key, ttl, json.dumps(doc))
    await r.sadd(f"rag:idx:{collection}", doc_id)

    try:
        from app.ann_index import get_ann_index  # external dependency

        ann = get_ann_index()
        await ann.bump_version(collection)
    except Exception as e:
        logger.debug("ANN version bump skipped: %s", e)

    try:
        from rmi_rag.splade_bm25 import _bm25_built_at, _bm25_index  # external dependency

        if _bm25_index is not None:
            _bm25_built_at = 0
            logger.debug("BM25 index cache invalidated for incremental rebuild")
    except Exception as e:
        logger.debug("BM25 cache invalidation skipped: %s", e)

    try:
        from app.entity_extraction import EntityLookup, extract_entities  # external dependency

        entities = extract_entities(content)
        if entities.has_entities:
            lookup = EntityLookup.get_instance()
            for entity in entities.all_entities():
                await lookup.index_entity(
                    entity["type"],
                    entity["value"],
                    doc_id,
                    metadata={**metadata, "collection": collection},
                )
            logger.debug("Entity indexing: %s entities", len(entities.all_entities()))
    except Exception as e:
        logger.debug("Entity indexing skipped: %s", e)

    try:
        from app.knowledge_graph import get_knowledge_graph  # external dependency

        kg = await get_knowledge_graph()
        await kg.ingest_rag_document(collection, doc_id, content, metadata)
        logger.debug("KG edge creation complete")
    except Exception as e:
        logger.debug("KG edge creation skipped: %s", e)

    logger.debug("pgvector upsert skipped (FAISS primary)")

    try:
        from app.rag.parent_retriever import get_parent_retriever  # external dependency

        pr = await get_parent_retriever()
        await pr.store_parent(doc_id=doc_id, content=content, metadata=metadata)
    except Exception as e:
        logger.debug("ParentRetriever.store_parent skipped: %s", e)

    logger.info("Ingested %s/%s: %s...", collection, doc_id, content[:60])
    return {"id": doc_id, "dims": result.dims, "collection": collection}


async def ingest_forensic_report(
    report_text: str,
    report_name: str = "forensic_report",
    metadata: dict | None = None,
) -> dict:
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


# ═══════════════════════════════════════════════════════════════════
# STATS
# ═══════════════════════════════════════════════════════════════════


async def get_stats() -> dict[str, Any]:
    embedder = await get_embedder()
    r = await _get_redis()

    ann_stats = {}
    try:
        from app.ann_index import get_ann_index  # external dependency

        ann = get_ann_index()
        ann_stats = ann.stats()
    except Exception:
        logger.warning("swallowed exception", exc_info=True)

    from rmi_rag.crypto_embeddings import COLLECTIONS  # noqa: F811 - reimport for local scope

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
        from app.semantic_cache import get_semantic_cache  # external dependency

        cache = get_semantic_cache()
        cache_stats = await cache.stats()
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
    from app.scam_sources import GoPlusConnector  # external dependency

    result = await GoPlusConnector.check_token(chain, token_address)
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

    return result
