"""Dual-tier Qdrant schema — crypto_intelligence collection.

Creates the unified collection with two named vectors:
  - qwen_vector   (2560-dim, Qwen3-Embedding-4B  — deep tier, :8004)
  - speed_vector  (1024-dim, Qwen3-Embedding-0.6B — consumer chat tier, :8007)

NOTE: the original design doc specified qwen_vector size=3584 — that is
incorrect per the official Qwen3 model card (Qwen3-Embedding-4B = 2560 dims).
This script uses verified dimensions from live endpoints.

Run once:  python -m scripts.dual_tier.schema_setup
Verify:    GET http://localhost:6333/collections/crypto_intelligence
"""

from __future__ import annotations

import os
import sys

from qdrant_client import QdrantClient, models

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.getenv("RAG_COLLECTION", "crypto_intelligence")

# Verified against live endpoints 2026-08-23:
#   :8004 (deep)  -> 2560 dims   :8007 (speed) -> 1024 dims
QWEN_DIMS = int(os.getenv("QWEN_DIMS", "2560"))
SPEED_DIMS = int(os.getenv("SPEED_DIMS", "1024"))


def initialize_database(client: QdrantClient | None = None) -> None:
    owns_client = client is None
    client = client or QdrantClient(url=QDRANT_URL)

    if client.collection_exists(COLLECTION):
        print(f"collection '{COLLECTION}' already exists — verifying vectors")
        info = client.get_collection(COLLECTION)
        cfg = info.config.params.vectors
        have = {name: v.size for name, v in cfg.items()} if isinstance(cfg, dict) else {}
        expected = {"qwen_vector": QWEN_DIMS, "speed_vector": SPEED_DIMS}
        for name, size in expected.items():
            if have.get(name) != size:
                print(f"  MISMATCH {name}: have={have.get(name)} want={size}")
                sys.exit(1)
        print("  dual-vector layout verified:", have)
    else:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config={
                "qwen_vector": models.VectorParams(size=QWEN_DIMS, distance=models.Distance.COSINE),
                "speed_vector": models.VectorParams(
                    size=SPEED_DIMS, distance=models.Distance.COSINE
                ),
            },
        )
        print(
            f"dual-vector collection '{COLLECTION}' created "
            f"(qwen_vector={QWEN_DIMS}, speed_vector={SPEED_DIMS})"
        )

    if owns_client:
        client.close()


if __name__ == "__main__":
    initialize_database()
