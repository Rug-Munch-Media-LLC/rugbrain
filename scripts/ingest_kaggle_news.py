#!/usr/bin/env python3
"""Ingest Kaggle news datasets into RugBrain news_v2_8b collection.

Datasets:
- jasmine2k26/cryptopulse-global-cryptocurrency-news-dataset (CryptoPulse)
- kaballa/cryptoner-ml-model (CryptoNER - includes entity labels)
- gsampathkumar01/bitcoin-news-dataset-newsapi (Bitcoin news via NewsAPI)

Each article is chunked into 500-char windows with 50-char overlap,
embedded with Qwen3-Embedding-8B (4096-dim via DeepInfra/local),
and upserted to the news_v2_8b collection with payload matching the
news_v2 schema (title, text, url, source, published, metadata).

Entity-aware handling: For CryptoNER dataset, the 'Currencies' field
is stored in metadata as entity labels.

Usage:
    python scripts/ingest_kaggle_news.py --dataset cryptopulse --dry-run
    python scripts/ingest_kaggle_news.py --dataset all --limit 100
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import logging
import os
from typing import Any

from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.qwen_embedders import Qwen3Embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_kaggle_news")

# Collection name from registry (news_v2 returns 404, use news_v2_8b)
COLLECTION = "news_v2_8b"
SOURCE_PREFIX = "kaggle"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
MAX_TEXT_LENGTH = 8000  # For embedding input limit

# Dataset configurations
DATASETS = {
    "cryptopulse": {
        "path": "data/raw/kaggle/cryptopulse/crypto_news_dataset.csv",
        "title_field": "title",
        "text_fields": ["title", "description"],  # We'll combine title + description
        "url_field": "url",
        "source_field": "news_provider",
        "date_field": "createdAt",
        "entity_field": None,  # No explicit entity field
        "has_header": True,
    },
    "cryptoner": {
        "path": "data/raw/kaggle/cryptoner/articlesData.csv",
        "title_field": "Title",  # This contains the main article text
        "text_fields": ["Title"],  # Only title available as text
        "url_field": "URL",
        "source_field": "Source",
        "date_field": "Created_at",
        "entity_field": "Currencies",  # Entity labels for NER
        "has_header": True,
    },
    "bitcoinnews": {
        "path": "data/raw/kaggle/bitcoinnews/bitcoin.csv",
        "title_field": "title",
        "text_fields": ["title", "description", "content"],  # Combine all text
        "url_field": "url",
        "source_field": "source_name",
        "date_field": "publishedAt",
        "entity_field": None,  # No explicit entity field
        "has_header": True,
    },
}


def _stable_news_id(source: str, url: str) -> str:
    """Generate stable news_id from source and URL."""
    h = hashlib.sha256(f"{source}:{url}".encode()).hexdigest()[:24]
    return f"{SOURCE_PREFIX}:{source}:{h}"


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Sliding-window chunker: size-char windows with overlap-char overlap."""
    text = " ".join(text.split())  # Normalize whitespace
    if not text:
        return []
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    chunks: list[str] = []
    for i in range(0, len(text), step):
        chunk = text[i : i + size].strip()
        if len(chunk) >= overlap:  # Drop tiny trailing slivers
            chunks.append(chunk)
    return chunks


def _chunk_point_id(news_id: str, idx: int) -> str:
    """Deterministic UUIDv5 for chunk point ID."""
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"news:{news_id}:{idx}"))


def _parse_date(date_str: str) -> str:
    """Parse date string to ISO format, fallback to current time."""
    from datetime import datetime

    try:
        # Try common formats
        for fmt in (
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%a, %d %b %Y %H:%M:%S %Z",
        ):
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.isoformat() + "Z"
            except ValueError:
                continue
        # If none work, return as-is (might already be ISO)
        return date_str
    except Exception:
        # Fallback to current time
        return datetime.utcnow().isoformat() + "Z"


def _extract_entities(entity_field_value: str | None) -> list[str]:
    """Extract entity labels from entity field value."""
    if not entity_field_value:
        return []
    # Split by common delimiters and clean
    entities = []
    for part in entity_field_value.replace(",", " ").split():
        cleaned = part.strip().upper()
        if cleaned and len(cleaned) >= 2:  # Basic filter
            entities.append(cleaned)
    return list(dict.fromkeys(entities))  # Deduplicate while preserving order


def _load_csv_rows(filepath: str, has_header: bool) -> list[dict[str, Any]] | None:
    """Read a dataset CSV into row dicts, or ``None`` when the file is absent.

    Blocking file I/O — callers on the event loop go through ``asyncio.to_thread``.
    """
    if not os.path.exists(filepath):
        return None
    with open(filepath, encoding="utf-8") as f:
        # Handle potential BOM
        if f.read(1) == "\ufeff":
            f.seek(0)  # Reset after BOM
        else:
            f.seek(0)

        return list(csv.DictReader(f)) if has_header else []


async def _ingest_dataset(
    dataset_name: str,
    limit: int | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Ingest a single dataset."""
    config = DATASETS[dataset_name]
    filepath = os.path.join(os.path.dirname(__file__), "..", config["path"])

    rows = await asyncio.to_thread(_load_csv_rows, filepath, config["has_header"])
    if rows is None:
        logger.error(f"Dataset file not found: {filepath}")
        return {"status": "error", "message": f"File not found: {filepath}"}

    stats: dict[str, Any] = {
        "dataset": dataset_name,
        "rows_read": 0,
        "articles_processed": 0,
        "chunks_created": 0,
        "points_upserted": 0,
        "duplicates_skipped": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    try:
        # Initialize embedder and store
        embedder = Qwen3Embedder()
        store = QdrantVectorStore()

        if not config["has_header"]:
            # Handle headerless case if needed
            logger.warning(f"Headerless CSV not implemented for {dataset_name}")
            return stats

        if limit:
            rows = rows[:limit]

        stats["rows_read"] = len(rows)
        logger.info(f"Processing {len(rows)} rows from {dataset_name}")

        # Process each row
        for row_idx, row in enumerate(rows):
            try:
                # Extract fields
                title = row.get(config["title_field"], "").strip()
                if not title:
                    stats["errors"] += 1
                    continue

                # Get text fields for chunking
                text_parts = []
                for field in config["text_fields"]:
                    value = row.get(field, "").strip()
                    if value:
                        text_parts.append(value)
                full_text = " ".join(text_parts)
                if not full_text:
                    stats["errors"] += 1
                    continue

                # Get URL for news_id
                url = row.get(config["url_field"], "").strip()
                if not url:
                    stats["errors"] += 1
                    continue

                # Generate stable news_id
                source = row.get(config["source_field"], "").strip() or dataset_name
                news_id = _stable_news_id(source, url)

                # Parse date
                date_str = row.get(config["date_field"], "")
                published_at = _parse_date(date_str) if date_str else ""

                # Extract entities for CryptoNER
                entities = []
                if config["entity_field"]:
                    entity_value = row.get(config["entity_field"], "")
                    entities = _extract_entities(entity_value)

                # Chunk the text
                chunks = _chunk_text(full_text)
                if not chunks:
                    stats["errors"] += 1
                    continue

                stats["articles_processed"] += 1
                stats["chunks_created"] += len(chunks)

                if dry_run:
                    continue

                # Process each chunk
                for chunk_idx, chunk in enumerate(chunks):
                    if len(chunk) < 10:  # Skip very short chunks
                        continue

                    # Generate deterministic point ID
                    point_id = _chunk_point_id(news_id, chunk_idx)

                    # Check if point already exists (deduplication)
                    has_point = await store.has_point(point_id, COLLECTION)
                    if has_point:
                        stats["duplicates_skipped"] += 1
                        continue

                    # Embed chunk
                    try:
                        embedding = await embedder.embed_one(chunk[:MAX_TEXT_LENGTH])
                        if not embedding or len(embedding) == 0:
                            stats["errors"] += 1
                            continue
                    except Exception as e:
                        logger.warning(f"Embedding failed for {point_id}: {e}")
                        stats["errors"] += 1
                        continue

                    # Prepare payload
                    payload = {
                        "title": title[:500],  # Limit title length
                        "text": chunk,
                        "url": url[:2048],
                        "source": source[:200],
                        "published_at": published_at,
                        "category": "crypto",  # Default category for news
                        "sentiment": None,  # News ingestion doesn't provide sentiment
                        "chains_mentioned": [],  # Not available in these datasets
                        "tokens_mentioned": [],  # Not available in these datasets
                        "news_id": news_id,
                        "rating": {},  # Empty rating payload
                        "quality_score": 50,  # Neutral quality score
                    }

                    # Add entity labels for CryptoNER
                    if entities:
                        payload["metadata"] = {"entity_labels": entities}
                    else:
                        payload["metadata"] = {}

                    # Insert into Qdrant
                    try:
                        success = await store.insert(
                            doc_id=point_id,
                            collection=COLLECTION,
                            embedding=embedding,
                            content=chunk,
                            metadata=payload["metadata"],
                            source=source,
                            severity="info",
                            chain="",  # News doesn't have chain info
                        )
                        if success:
                            stats["points_upserted"] += 1
                        else:
                            stats["errors"] += 1
                    except Exception as e:
                        logger.warning(f"Insert failed for {point_id}: {e}")
                        stats["errors"] += 1

                # Progress logging
                if (row_idx + 1) % 50 == 0 or (row_idx + 1) == len(rows):
                    logger.info(
                        f"Progress {dataset_name}: {row_idx + 1}/{len(rows)} rows, "
                        f"{stats['points_upserted']} points upserted, "
                        f"{stats['duplicates_skipped']} duplicates skipped"
                    )

            except Exception as e:
                logger.warning(f"Error processing row {row_idx}: {e}")
                stats["errors"] += 1
                continue

    except Exception as e:
        logger.error(f"Failed to ingest dataset {dataset_name}: {e}")
        stats["status"] = "error"
        stats["message"] = str(e)
        return stats

    finally:
        # Cleanup
        if "embedder" in locals():
            await embedder.close() if hasattr(embedder, "close") else None
        if "store" in locals():
            await store.close()

    return stats


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Ingest Kaggle news datasets")
    parser.add_argument(
        "--dataset",
        choices=["cryptopulse", "cryptoner", "bitcoinnews", "all"],
        default="all",
        help="Dataset to ingest",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of rows per dataset (for testing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and process but do not write to Qdrant",
    )
    args = parser.parse_args()

    datasets_to_process = (
        ["cryptopulse", "cryptoner", "bitcoinnews"] if args.dataset == "all" else [args.dataset]
    )

    total_stats = {
        "datasets_processed": 0,
        "total_rows": 0,
        "total_articles": 0,
        "total_chunks": 0,
        "total_points": 0,
        "total_duplicates": 0,
        "total_errors": 0,
    }

    for dataset_name in datasets_to_process:
        logger.info(f"Starting ingestion for {dataset_name}")
        stats = await _ingest_dataset(dataset_name, args.limit, args.dry_run)

        if stats.get("status") == "error":
            logger.error(f"Failed to process {dataset_name}: {stats.get('message')}")
            continue

        total_stats["datasets_processed"] += 1
        total_stats["total_rows"] += stats.get("rows_read", 0)
        total_stats["total_articles"] += stats.get("articles_processed", 0)
        total_stats["total_chunks"] += stats.get("chunks_created", 0)
        total_stats["total_points"] += stats.get("points_upserted", 0)
        total_stats["total_duplicates"] += stats.get("duplicates_skipped", 0)
        total_stats["total_errors"] += stats.get("errors", 0)

        logger.info(
            f"Completed {dataset_name}: {stats.get('articles_processed')} articles, "
            f"{stats.get('points_upserted')} points upserted, "
            f"{stats.get('duplicates_skipped')} duplicates skipped"
        )

    # Print summary
    print("\n" + "=" * 60)
    print("KAGGLE NEWS INGESTION SUMMARY")
    print("=" * 60)
    print(f"Datasets processed: {total_stats['datasets_processed']}")
    print(f"Total rows read: {total_stats['total_rows']}")
    print(f"Total articles processed: {total_stats['total_articles']}")
    print(f"Total chunks created: {total_stats['total_chunks']}")
    print(f"Total points upserted: {total_stats['total_points']}")
    print(f"Total duplicates skipped: {total_stats['total_duplicates']}")
    print(f"Total errors: {total_stats['total_errors']}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
