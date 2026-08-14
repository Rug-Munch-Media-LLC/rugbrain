"""
Rug Munch Intelligence - RAG Ingestion Pipeline
=================================================
Nightly indexing of ALL data sources into the RAG system.
Feeds: news, CT rundown, market data, social metrics, on-chain data.

Runs at 3AM UTC. Embeds via Infinity BAAI/bge-m3 (1024d).
Redis-backed with permanence to B2.

Incremental indexing: each successful ingest is tracked in a Redis delta
queue so the system knows which docs changed since the last full rebuild.
"""

# NOTE (deprecated): archived in _legacy/ — kept for reference only, has zero
# live references, and is not type-checked in CI. Per-line pyright ignores
# below silence diagnostics that would otherwise require porting private
# monorepo dependencies that do not exist in the OSS rugbrain package.

import hashlib
import json
import logging
import os
from datetime import UTC, datetime

logger = logging.getLogger("rag_ingestion")


async def nightly_rag_index(**kw) -> dict:
    """Nightly RAG indexing - embeds all new content from all sources.

    Called by cron at 3AM UTC. Idempotent - only indexes new content.
    """
    indexed = {"collections": {}, "total_docs": 0, "errors": []}

    try:
        # ── 1. Index recent news articles ──
        from app.domains.databus.news_intel import aggregate_all_news  # external dependency

        news = await aggregate_all_news(limit=100)
        news_docs = []
        for article in news.get("articles", [])[:50]:
            h = hashlib.sha256(
                (article.get("url", "") + article.get("title", "")).encode()
            ).hexdigest()[:12]
            news_docs.append(
                {
                    "id": f"news:{h}",
                    "text": f"{article.get('title', '')} {article.get('summary', '')[:500]}",
                    "metadata": {
                        "source": article.get("source", ""),
                        "categories": article.get("categories", []),
                        "sentiment": article.get("sentiment", {}).get("sentiment", ""),
                        "quality": article.get("quality_score", 0),
                        "published": article.get("published", ""),
                    },
                }
            )

        indexed["collections"]["news_articles"] = await _embed_batch(news_docs, "news_articles")
        indexed["total_docs"] += indexed["collections"]["news_articles"]
        logger.info("RAG indexed %s news articles", indexed["collections"]["news_articles"])

    except Exception as e:
        indexed["errors"].append(f"news: {str(e)[:100]}")

    try:
        # ── 2. Index CT Rundown ──
        from app.domains.databus.x_intel import fetch_ct_rundown  # external dependency

        ct = await fetch_ct_rundown(limit=30)
        ct_docs = []
        for story in ct.get("rundown", [])[:20]:
            h = hashlib.sha256((story.get("url", "") + story.get("text", "")).encode()).hexdigest()[
                :12
            ]
            ct_docs.append(
                {
                    "id": f"ct:{h}",
                    "text": f"@{story.get('author_handle', '')}: {story.get('text', '')[:400]}",
                    "metadata": {
                        "source": "ct_rundown",
                        "author": story.get("author_handle", ""),
                        "category": story.get("category", ""),
                        "ct_score": story.get("ct_score", 0),
                        "engagement": story.get("engagement", {}),
                    },
                }
            )

        indexed["collections"]["ct_rundown"] = await _embed_batch(ct_docs, "ct_rundown")
        indexed["total_docs"] += indexed["collections"]["ct_rundown"]
        logger.info("RAG indexed %s CT stories", indexed["collections"]["ct_rundown"])

    except Exception as e:
        indexed["errors"].append(f"ct: {str(e)[:100]}")

    try:
        # ── 3. Index market data snapshot ──
        from app.domains.databus.news_provider import (  # external dependency
            get_fear_greed,
            get_market_brief,
        )

        market = await get_market_brief()
        fear = await get_fear_greed()

        market_doc = {
            "id": f"market:{datetime.now(UTC).strftime('%Y%m%d')}",
            "text": market.get("brief", "") + f" Fear & Greed: {fear.get('value', 50)}",
            "metadata": {
                "source": "market_brief",
                "fear_greed": fear.get("value", 50),
                "classification": fear.get("classification", ""),
                "date": datetime.now(UTC).isoformat(),
            },
        }
        indexed["collections"]["market_intel"] = await _embed_batch([market_doc], "market_intel")
        indexed["total_docs"] += indexed["collections"]["market_intel"]

    except Exception as e:
        indexed["errors"].append(f"market: {str(e)[:100]}")

    try:
        # ── 4. Index social metrics ──
        from app.domains.databus.social_intel import get_social_metrics  # external dependency

        social = await get_social_metrics()
        social_doc = {
            "id": f"social:{datetime.now(UTC).strftime('%Y%m%d')}",
            "text": json.dumps(social, default=str)[:2000],
            "metadata": {
                "source": "social_metrics",
                "trending": list(social.get("trending_topics", {}).keys())[:5],
                "sentiment": social.get("market_sentiment", {}).get("dominant", ""),
                "date": datetime.now(UTC).isoformat(),
            },
        }
        indexed["collections"]["social_intel"] = await _embed_batch([social_doc], "social_intel")
        indexed["total_docs"] += indexed["collections"]["social_intel"]

    except Exception as e:
        indexed["errors"].append(f"social: {str(e)[:100]}")

    indexed["completed_at"] = datetime.now(UTC).isoformat()
    indexed["source"] = "rag_ingestion"

    return indexed


async def _embed_batch(docs: list[dict], collection: str) -> int:
    """Embed a batch of documents and store in Redis RAG store."""
    if not docs:
        return 0

    try:
        import redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=5,
        )

        embedded = 0
        for doc in docs:
            doc_id = doc["id"]
            # Check if already indexed
            if r.exists(f"rag:doc:{collection}:{doc_id}"):
                continue

            # Compute the embedding vector for this document.
            # Embedder outages degrade gracefully: the doc is still stored
            # with text+metadata, just without a vector.
            embedding: list[float] | None = None
            try:
                from rugbrain.embedder import get_embedder

                embedder = get_embedder()
                embedding = await embedder.embed_one(doc["text"])
            except Exception as e:
                logger.warning("embed_failed doc_id=%s collection=%s err=%s", doc_id, collection, e)

            # Store document metadata (+ vector when available)
            mapping = {
                "text": doc["text"][:2000],
                "metadata": json.dumps(doc.get("metadata", {}), default=str),
                "indexed_at": datetime.now(UTC).isoformat(),
            }
            if embedding:
                mapping["embedding"] = json.dumps(embedding)
            r.hset(
                f"rag:doc:{collection}:{doc_id}",
                mapping=mapping,
            )
            # Set TTL: 30 days
            r.expire(f"rag:doc:{collection}:{doc_id}", 2592000)
            embedded += 1

            # Store parent document for context expansion
            try:
                from app.rag.parent_retriever import get_parent_retriever  # external dependency

                pr = await get_parent_retriever()
                await pr.store_parent(
                    doc_id=doc_id,
                    content=doc["text"],
                    metadata=doc.get("metadata", {}),
                )
            except Exception as e:
                logger.debug("parent_store_failed doc_id=%s err=%s", doc_id, e)

            try:
                from app.rag.incremental_indexer import DeltaTracker  # external dependency

                DeltaTracker.track_add(doc_id, collection)
            except Exception as e:
                logger.debug(
                    "delta_track_failed doc_id=%s collection=%s err=%s", doc_id, collection, e
                )

        r.close()
        return embedded

    except Exception as e:
        logger.warning("Embed batch failed for %s: %s", collection, e)
        return 0


async def rag_health_check(**kw) -> dict:
    """Check RAG system health - collections, doc counts, storage."""
    try:
        import redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=5,
        )

        collections = [
            "news_articles",
            "ct_rundown",
            "market_intel",
            "social_intel",
            "wallet_profiles",
            "token_analysis",
            "scam_patterns",
            "forensic_reports",
            "contract_audits",
            "known_scams",
        ]

        stats = {}
        total = 0
        for col in collections:
            keys = r.keys(f"rag:doc:{col}:*")
            count = len(keys)
            stats[col] = count
            total += count

        r.close()

        return {
            "status": "healthy",
            "total_documents": total,
            "collections": stats,
            "embedder": "BAAI/bge-m3 (Infinity, 1024d)",
            "storage": "Redis + B2 permanence",
            "nightly_cron": "3AM UTC",
            "checked_at": datetime.now(UTC).isoformat(),
            "source": "rag_health",
        }

    except Exception as e:
        return {"status": "error", "error": str(e)[:200], "source": "rag_health"}
