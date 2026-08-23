#!/usr/bin/env python3
"""Shared helpers for the RugBrain contract-knowledge ingesters.

Used by ``ingest_codebase_knowledge.py``, ``ingest_security_patterns.py`` and
``ingest_good_bad_contracts.py`` so every script uses the same batch
embedding path (2560-dim qwen3-embedding-4b), deterministic UUIDv5 point IDs, and
canonical Qdrant payload shape.

Scripts import this module directly:

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from ingest_common import embed_batch, content_hash, to_uuid

No secrets: all defaults point at local services (Qdrant / ) and are
overridable via the standard RAG env vars (QDRANT_URL, QWEN_EMBED_URL,
RAG_EMBED_MODEL).
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from collections.abc import Sequence
from typing import Any

import httpx

logger = logging.getLogger(__name__)

QWEN_EMBED_URL = os.getenv("QWEN_EMBED_URL", "http://172.22.0.1:8004/v1").rstrip("/")
QWEN_EMBED_MODEL_NAME = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-4b")
EMBEDDING_DIM = 2560

# Long code texts embed slowly; batch 8 long texts (matches seed_qdrant).
EMBED_BATCH = 8


def to_uuid(doc_id: str) -> str:
    """Deterministic UUIDv5 — matches ``QdrantVectorStore._to_uuid``."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))


def content_hash(text: str) -> str:
    """Stable content fingerprint (sha256 prefix) for point IDs."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def chunk_text(text: str, size: int = 2000, overlap: int = 100) -> list[str]:
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
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


async def embed_batch(client: httpx.AsyncClient, texts: Sequence[str]) -> list[list[float]]:
    """Embed *texts* via /embeddings, one request per batch.

    Returns one 2560-dim vector per text; failed/short vectors become an
    empty list entry so callers can skip them deterministically.
    """
    if not texts:
        return []
    payload = {"model": QWEN_EMBED_MODEL_NAME, "input": [t[:8000] for t in texts]}
    try:
        r = await client.post(f"{QWEN_EMBED_URL}/embeddings", json=payload, timeout=900.0)
        if r.status_code != 200:
            logger.warning("embed_http_fail status=%d body=%s", r.status_code, r.text[:200])
            return []
        data = r.json()
        items = sorted(data.get("data", []), key=lambda i: i.get("index", 0))
        out: list[list[float]] = []
        for item in items:
            vec = item.get("embedding", [])
            if isinstance(vec, list) and len(vec) == EMBEDDING_DIM:
                out.append([float(x) for x in vec])
            else:
                out.append([])
        return out
    except Exception as e:
        logger.warning("embed_fail: %s", str(e)[:200])
        return []


async def embed_many(client: httpx.AsyncClient, texts: Sequence[str]) -> list[list[float]]:
    """Embed *texts* in batches; returns vectors aligned with input order."""
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        vectors.extend(await embed_batch(client, texts[i : i + EMBED_BATCH]))
    return vectors


def build_payload(
    doc_id: str,
    vector: list[float],
    content: str,
    metadata: dict[str, Any] | None = None,
    source: str = "",
    severity: str = "medium",
    chain: str = "",
    collection: str = "",
) -> dict[str, Any]:
    """Build a single Qdrant upsert point (matches QdrantVectorStore shape)."""
    return {
        "id": to_uuid(doc_id),
        "vector": vector,
        "payload": {
            "content": content[:10000],
            "metadata": metadata or {},
            "source": source,
            "severity": severity,
            "chain": chain,
            "collection": collection,
        },
    }
