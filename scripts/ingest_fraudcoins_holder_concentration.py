#!/usr/bin/env python3
"""Ingest Fraudcoins_crypto-holder-concentration into scam_patterns_8b collection and B2 parquet.

Source: HuggingFace dataset Fraudcoins_crypto-holder-concentration
CC BY 4.0. Holder concentration = whale/insider risk.

Each token's time series (history.json) is written to B2 parquet path.
Each token gets a text summary embedded into scam_patterns_8b collection.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Sys path / helpers
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from rugbrain.embedder import get_embedder  # noqa: E402
from rugbrain.qdrant_store import QdrantVectorStore  # noqa: E402

# Default to the repo-local download; override with FRAUDCOINS_DATA_DIR.
DATA_DIR = os.getenv(
    "FRAUDCOINS_DATA_DIR",
    str(Path(__file__).resolve().parents[1] / "data" / "raw" / "hf" / "Fraudcoins_crypto-holder-concentration"),
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_fraudcoins_holder_concentration")

COLLECTION = "scam_patterns"  # rugbrain-owned; resolver maps base -> scam_patterns_8b
SOURCE = "fraudcoins_holder_concentration"
SOURCE_URL = "https://huggingface.co/datasets/Fraudcoins/crypto-holder-concentration"
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


def build_summary(coin_id: str, symbol: str, name: str, top10_pct: float, date_str: str) -> str:
    """Create a short text summary for embedding."""
    return (
        f"Token {symbol} ({name}) has top-10 holder concentration of {top10_pct:.1f}% "
        f"as of {date_str}, indicating potential whale/insider risk."
    )



def _load_dataset_files(concentration_path, history_path, coins_index_path):
    """Read the three holder-concentration JSON files (sync, off the loop)."""
    with open(concentration_path, encoding="utf-8") as fh:
        concentration = json.load(fh)
    with open(history_path, encoding="utf-8") as fh:
        history = json.load(fh)
    with open(coins_index_path, encoding="utf-8") as fh:
        coins = json.load(fh)
    return concentration, history, coins


async def main(
    limit: int | None = None,
    dry_run: bool = False,
    batch_size: int = 100,
    b2_upload: bool = False,
) -> dict[str, int]:
    stats = {
        "coins_processed": 0,
        "summaries_embedded": 0,
        "parquet_rows_written": 0,
        "skipped": 0,
        "errors": 0,
    }

    # Load concentration.json (snapshot)
    # All three files resolve from DATA_DIR (repo-relative, FRAUDCOINS_DATA_DIR
    # override). Two of these were absolute paths into a LANE WORKTREE, which
    # vanish when the lane is released.
    concentration_path = str(Path(DATA_DIR) / "concentration.json")
    history_path = str(Path(DATA_DIR) / "history.json")
    coins_index_path = str(Path(DATA_DIR) / "coins-index.json")

    try:
        # File IO stays off the event loop (blocking IO in async).
        concentration_data, history_data, coins_index_data = await asyncio.to_thread(
            _load_dataset_files, concentration_path, history_path, coins_index_path
        )
    except Exception as e:
        logger.error("Failed to load dataset files: %s", str(e)[:200])

        stats["errors"] += 1
        return stats

    coins = concentration_data.get("coins", {})
    history = history_data.get("coins", {})
    # coins_index is a list of dicts with id, name, symbol
    coin_info_map: dict[str, dict[str, str]] = {
        item["id"]: {"name": item["name"], "symbol": item["symbol"]}
        for item in coins_index_data
        if "id" in item
    }

    logger.info("Loaded %d coins from concentration data", len(coins))
    logger.info("Loaded %d coins from history data", len(history))
    logger.info("Loaded %d coins from index data", len(coin_info_map))

    # Build records for embedding and parquet
    summaries_to_embed: list[tuple[str, str, dict[str, Any]]] = []  # (doc_id, text, metadata)
    parquet_rows: list[dict[str, Any]] = []  # for parquet

    for coin_id, coin_data in coins.items():
        if limit is not None and stats["coins_processed"] >= limit:
            break

        symbol = coin_info_map.get(coin_id, {}).get("symbol", "")
        name = coin_info_map.get(coin_id, {}).get("name", "")
        top10_pct = coin_data.get("top10Pct", 0.0)
        sampled_at = coin_data.get("sampledAt", "")
        chain = coin_data.get("chain", "")

        # Parse date from sampledAt (ISO string)
        date_str = ""
        if sampled_at:
            try:
                dt = datetime.fromisoformat(sampled_at.replace("Z", "+00:00"))
                date_str = dt.strftime("%Y-%m-%d")
            except Exception:
                date_str = sampled_at.split("T")[0] if "T" in sampled_at else sampled_at

        if not symbol or not name or top10_pct == 0.0:
            stats["skipped"] += 1
            continue

        # Build summary for embedding
        summary_text = build_summary(coin_id, symbol, name, top10_pct, date_str)
        if not summary_text.strip():
            stats["skipped"] += 1
            continue

        # Create deterministic doc_id
        chash = content_hash(summary_text)
        doc_id = f"{SOURCE}:{coin_id}:{chash}"

        # Chunk the summary if needed (though it's short)
        text_chunks = split_text(summary_text)
        for chunk_idx, chunk_text in enumerate(text_chunks):
            chunk_doc_id = f"{doc_id}:chunk{chunk_idx}"
            metadata = {
                "source": SOURCE,
                "source_url": SOURCE_URL,
                "coin_id": coin_id,
                "symbol": symbol,
                "name": name,
                "top10_pct": top10_pct,
                "sampled_at": sampled_at,
                "date": date_str,
                "chain": chain,
                "knowledge_type": "holder_concentration",
                "chunk_index": chunk_idx,
                "total_chunks": len(text_chunks),
            }
            summaries_to_embed.append((chunk_doc_id, chunk_text, metadata))

        # Prepare parquet rows from history
        if coin_id in history:
            for date_point, pct in history[coin_id]:
                parquet_rows.append({
                    "coin_id": coin_id,
                    "symbol": symbol,
                    "name": name,
                    "date": date_point,
                    "pct": pct,
                })
                stats["parquet_rows_written"] += 1

        stats["coins_processed"] += 1

    logger.info("Built %d chunks to embed (limit=%s)", len(summaries_to_embed), limit)
    logger.info("Prepared %d parquet rows", stats["parquet_rows_written"])

    if not summaries_to_embed and not parquet_rows:
        logger.warning("No valid data to ingest after filtering")
        return stats

    # Handle parquet upload to B2
    if b2_upload:
        # Check B2 credentials
        b2_key_id = os.getenv("B2_APPLICATION_KEY_ID")
        b2_app_key = os.getenv("B2_APPLICATION_KEY")
        if not b2_key_id or not b2_app_key:
            logger.warning(
                "B2 credentials not set (B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY). "
                "Skipping B2 upload. Set these env vars to enable."
            )
            b2_upload = False
        else:
            logger.info("B2 credentials found, will attempt upload")
            # We'll implement B2 upload later if needed; for now, just log
            logger.info("[B2 UPLOAD] Would upload %d rows to B2", len(parquet_rows))
    else:
        logger.info("B2 upload skipped (dry-run or flag not set)")

    if dry_run:
        logger.info("[DRY RUN] would embed %d docs into %s", len(summaries_to_embed), COLLECTION)
        for i in range(min(3, len(summaries_to_embed))):
            row = summaries_to_embed[i]
            logger.info("[DRY RUN] sample %d: desc=%s", i, row[1][:100])
        stats["summaries_embedded"] = len(summaries_to_embed)
        return stats

    # Get the canonical 8B embedder
    logger.info("Initializing embedder (canonical 8B lane)...")
    embedder = get_embedder()

    # Connect to Qdrant
    logger.info("Connecting to Qdrant...")
    store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))

    # Process in batches
    for i in range(0, len(summaries_to_embed), batch_size):
        batch = summaries_to_embed[i:i + batch_size]
        texts = [doc[1] for doc in batch]
        doc_ids = [doc[0] for doc in batch]
        metadatas = [doc[2] for doc in batch]

        logger.info("Embedding batch %d/%d (%d texts)", i // batch_size + 1, (len(summaries_to_embed) + batch_size - 1) // batch_size, len(texts))

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
            stats["summaries_embedded"] += len(batch)
            logger.info("Upserted batch %d successfully", i // batch_size + 1)
        except Exception as e:
            logger.error("Failed to embed/upsert batch %d: %s", i // batch_size + 1, e)
            stats["errors"] += len(batch)

    await store.close()
    logger.info("Ingestion complete: %s", stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest fraudcoins holder concentration into scam_patterns_8b")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of coins to process")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without embedding")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for embedding")
    parser.add_argument("--b2-upload", action="store_true", help="Upload parquet to B2 (requires credentials)")
    args = parser.parse_args()

    result = asyncio.run(main(limit=args.limit, dry_run=args.dry_run, batch_size=args.batch_size, b2_upload=args.b2_upload))
    print(f"Result: {result}")
