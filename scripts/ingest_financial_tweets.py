#!/usr/bin/env python3
"""Ingest TimKoornstra_financial-tweets-sentiment into news_v2_8b collection.

Source: HuggingFace dataset TimKoornstra/financial-tweets-sentiment
MIT licensed. Labelled tweet sentiment.

Each tweet becomes a document in news_v2_8b with metadata for filtering.
Deterministic doc_id = financial_tweets:{idx}:{content_hash} for idempotent re-runs.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import sys
from typing import Any

# Sys path / helpers
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from rugbrain.embedder import get_embedder  # noqa: E402
from rugbrain.qdrant_store import QdrantVectorStore  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_financial_tweets")

COLLECTION = "news_v2"  # rugbrain-owned; resolver maps base -> news_v2_8b
SOURCE = "financial_tweets_sentiment"
SOURCE_URL = "https://huggingface.co/datasets/TimKoornstra/financial-tweets-sentiment"
EMBED_DIM = 4096  # canonical qwen3-embedding-8b


def to_uuid(doc_id: str) -> str:
    """Deterministic UUIDv5 for Qdrant point IDs."""
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))


def content_hash(text: str) -> str:
    """Short content hash for deterministic doc_id."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def split_text(text: str, max_chars: int = 1000) -> list[str]:
    """Split text into chunks for embedding."""
    if len(text) <= max_chars:
        return [text]
    chunks = []
    for i in range(0, len(text), max_chars):
        chunks.append(text[i:i + max_chars])
    return chunks


async def main(
    limit: int | None = None,
    dry_run: bool = False,
    batch_size: int = 100,
) -> dict[str, int]:
    stats = {"fetched": 0, "embedded": 0, "skipped": 0, "errors": 0}

    # Lazy import datasets
    try:
        from datasets import load_dataset
    except ImportError:
        logger.error("datasets library not installed")
        stats["errors"] += 1
        return stats

    logger.info("Loading HuggingFace dataset: %s", SOURCE)
    try:
        ds = load_dataset('TimKoornstra/financial-tweets-sentiment', split='train')
        total_rows = len(ds)
        logger.info("Loaded dataset with %d rows", total_rows)
    except Exception as e:
        logger.error("Failed to load dataset: %s", str(e)[:200])
        stats["errors"] += 1
        return stats

    stats["fetched"] = total_rows

    if dry_run:
        logger.info("[DRY RUN] would embed %d docs into %s", min(total_rows, limit or total_rows), COLLECTION)
        for i in range(min(3, total_rows)):
            row = ds[i]
            logger.info("[DRY RUN] sample %d: tweet=%s sentiment=%s", i, row.get('tweet', '')[:100], row.get('sentiment'))
        # A dry run embeds nothing: reporting rows as "embedded" made the
        # dry-run and real runs indistinguishable in the stats block.
        return stats

    # Build doc records for embedding
    docs_to_embed: list[tuple[str, str, dict[str, Any]]] = []  # (doc_id, text, metadata)

    for idx, row in enumerate(ds):
        if limit is not None and idx >= limit:
            break

        tweet = row.get('tweet', '') or ''
        sentiment = row.get('sentiment', 'Unknown')
        url = row.get('url', '')

        # Use tweet as primary content
        content = tweet.strip()
        if not content or len(content) < 10:
            stats["skipped"] += 1
            continue

        # Create deterministic doc_id
        chash = content_hash(content)
        doc_id = f"{SOURCE}:{idx}:{chash}"

        # Chunk the content if needed
        text_chunks = split_text(content)
        for chunk_idx, chunk_text in enumerate(text_chunks):
            chunk_doc_id = f"{doc_id}:chunk{chunk_idx}"
            metadata = {
                "source": SOURCE,
                "source_url": SOURCE_URL,
                "tweet": content[:500],
                "sentiment": sentiment,
                "url": url[:500] if url else '',
                "knowledge_type": "financial_tweet_sentiment",
                "chunk_index": chunk_idx,
                "total_chunks": len(text_chunks),
            }
            docs_to_embed.append((chunk_doc_id, chunk_text, metadata))

    logger.info("Built %d chunks to embed (limit=%s)", len(docs_to_embed), limit)

    if not docs_to_embed:
        logger.warning("No valid chunks to ingest after filtering")
        return stats

    # Get the canonical 8B embedder
    logger.info("Initializing embedder (canonical 8B lane)...")
    embedder = get_embedder()

    # Connect to Qdrant
    logger.info("Connecting to Qdrant...")
    store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))

    # Process in batches
    for i in range(0, len(docs_to_embed), batch_size):
        batch = docs_to_embed[i:i + batch_size]
        texts = [doc[1] for doc in batch]
        doc_ids = [doc[0] for doc in batch]
        metadatas = [doc[2] for doc in batch]

        logger.info("Embedding batch %d/%d (%d texts)", i // batch_size + 1, (len(docs_to_embed) + batch_size - 1) // batch_size, len(texts))

        try:
            vectors = await embedder.embed(texts)
            for doc_id, vec, meta in zip(doc_ids, vectors, metadatas, strict=False):
                await store.insert(
                    doc_id=doc_id,
                    collection=COLLECTION,
                    embedding=vec,
                    content=meta.get("text", ""),
                    metadata=meta,
                    source=SOURCE,
                    severity="medium",
                    chain="multi",
                )
            stats["embedded"] += len(batch)
            logger.info("Upserted batch %d successfully", i // batch_size + 1)
        except Exception as e:
            logger.error("Failed to embed/upsert batch %d: %s", i // batch_size + 1, e)
            stats["errors"] += len(batch)

    await store.close()
    logger.info("Ingestion complete: %s", stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest financial-tweets-sentiment into news_v2_8b")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of source rows")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without embedding")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for embedding")
    args = parser.parse_args()

    result = asyncio.run(main(limit=args.limit, dry_run=args.dry_run, batch_size=args.batch_size))
    print(f"Result: {result}")
