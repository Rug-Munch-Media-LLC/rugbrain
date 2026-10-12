#!/usr/bin/env python3
"""Ingest Crypto Education Corpus from HuggingFace into education_8b collection.

Source: https://huggingface.co/datasets/kskada/crypto-education-en-corpus
A curated educational corpus of 3,487 documents (~3.6M words) sourced from major
crypto platforms (IQ Wiki, Binance Academy, Kraken, Coinbase, Gemini, Ethereum.org,
Investopedia). Includes markdown content, metadata, and related topics, designed for
RAG systems. Companion Golden Set provides 497 Q&A pairs for RAG evaluation.

Target: education_8b (5157 points, currently green; 4096-dim canonical 8B lane).

Each document is embedded (4096-dim qwen3-embedding-8b via DeepInfra primary,
llama.cpp :8004 fallback) and upserted with metadata: source, chunk_index, topic,
rel_key (deterministic UUIDv5 from source+chunk_id) for idempotent re-runs.

Idempotent: rel_key = f"education:{source_name}:{chunk_index}" — re-runs overwrite
the same Qdrant points. Uses the canonical 8B embedder (same as OFAC script).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

# ── Sys path / helpers ────────────────────────────────────────────────────
sys_path_scripts = os.path.dirname(os.path.abspath(__file__))
if sys_path_scripts not in sys.path:
    sys.path.insert(0, sys_path_scripts)

from rugbrain.embedder import get_embedder
from rugbrain.qdrant_store import QdrantVectorStore
import hashlib
import uuid

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("ingest_education")

COLLECTION = "education_8b"
SOURCE = "crypto_education_en_corpus"
SOURCE_URL = "https://huggingface.co/datasets/kskada/crypto-education-en-corpus"
EMBED_DIM = 4096  # canonical qwen3-embedding-8b


def to_uuid(doc_id: str) -> str:
    """Deterministic UUIDv5 — matches QdrantVectorStore._to_uuid."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))


def split_text(text: str, size: int = 2000, overlap: int = 100) -> list[str]:
    """Split *text* into fixed-width windows with overlap (no mid-token cuts)."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        start += size - overlap
    return chunks


async def run(dry_run: bool, limit: int | None) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "collection": COLLECTION,
        "source": SOURCE,
        "fetched": 0,
        "embedded": 0,
        "upserted": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    # Lazy import datasets to avoid overhead if not needed
    try:
        from datasets import load_dataset
    except ImportError:
        logger.error("datasets library not installed")
        stats["errors"] += 1
        return stats

    logger.info("Loading HuggingFace dataset: %s", SOURCE)
    try:
        ds = load_dataset('kskada/crypto-education-en-corpus', split='train')
        total_rows = len(ds)
        logger.info("Loaded dataset with %d rows", total_rows)
    except Exception as e:
        logger.error("Failed to load dataset: %s", str(e)[:200])
        stats["errors"] += 1
        return stats

    stats["fetched"] = total_rows

    if dry_run:
        logger.info("[DRY RUN] would embed %d docs into %s", total_rows, COLLECTION)
        for i in range(min(3, total_rows)):
            row = ds[i]
            logger.info("[DRY RUN] sample %d: title=%s source=%s", i, row.get('title', ''), row.get('source', ''))
        stats["embedded"] = total_rows
        return stats

    # Build doc records for embedding
    docs_to_embed: list[tuple[str, str, dict[str, Any]]] = []  # (doc_id, text, metadata)
    for idx, row in enumerate(ds):
        if limit is not None and idx >= limit:
            break

        content_parts = []
        if row.get('title'):
            content_parts.append(f"Title: {row['title']}")
        if row.get('markdown'):
            content_parts.append(f"Content: {row['markdown']}")
        content = "\n\n".join(content_parts) if content_parts else ""

        if not content or len(content.strip()) < 10:
            continue

        source_name = row.get('source', 'unknown').replace('.', '_').replace('/', '_')
        rel_key = f"education:{source_name}:{idx}"

        # Chunk the content
        text_chunks = split_text(content)
        for chunk_idx, chunk_text in enumerate(text_chunks):
            doc_id = f"{rel_key}:chunk{chunk_idx}"
            metadata = {
                "source": row.get('source', ''),
                "title": row.get('title', '')[:200],
                "url": row.get('url', '')[:500],
                "word_count": row.get('word_count', 0),
                "depth": row.get('depth', 0),
                "related_topics": row.get('related_topics', []),
                "chunk_index": chunk_idx,
                "total_chunks": len(text_chunks),
                "knowledge_type": "crypto_education",
            }
            docs_to_embed.append((doc_id, chunk_text, metadata))

    logger.info("Built %d chunks to embed (limit=%s)", len(docs_to_embed), limit)

    if not docs_to_embed:
        logger.warning("No valid chunks to ingest after filtering")
        return stats

    # Get the canonical 8B embedder
    embedder = get_embedder()

    # Embed and upsert
    store = QdrantVectorStore(os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))

    for doc_id, txt, metadata in docs_to_embed:
        try:
            vec = await embedder.embed([txt])
            if not vec or not vec[0]:
                stats["errors"] += 1
                continue
            vec = vec[0]

            # Verify dimension
            if len(vec) != EMBED_DIM:
                logger.warning("embed_dim_mismatch got=%d want=%d", len(vec), EMBED_DIM)
                stats["errors"] += 1
                continue

            ok = await store.insert(
                doc_id=doc_id,
                collection=COLLECTION,
                embedding=vec,
                content=txt,
                metadata=metadata,
                source=SOURCE,
                severity="medium",
                chain="multi",
            )
            if ok:
                stats["upserted"] += 1
            else:
                stats["errors"] += 1
        except Exception as e:
            logger.warning("embed_upsert_failed doc_id=%s err=%s", doc_id[:40], str(e)[:120])
            stats["errors"] += 1
            continue

    logger.info(
        "education_done fetched=%d embedded=%d upserted=%d errors=%d",
        stats["fetched"],
        stats["embedded"],
        stats["upserted"],
        stats["errors"],
    )

    await store.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Crypto Education Corpus → education_8b")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + embed but do not upsert")
    parser.add_argument("--limit", type=int, default=None, help="Max docs to ingest (default: all)")
    args = parser.parse_args()

    stats = asyncio.run(run(args.dry_run, args.limit))
    import json

    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
