"""Embedding model fine-tuning pipeline for crypto scam domain.

Collects labeled scam/hack documents from Qdrant collections,
exports training pairs, and provides a pipeline for domain-specific
fine-tuning of embedding models.

Supports:
- Positive pairs: confirmed scam documents with similar patterns
- Hard negatives: known-scam vs legitimate token documents
- Export to HuggingFace datasets format for training
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger("rag.fine_tuning")


class EmbeddingTrainingData:
    def __init__(self, output_dir: str = "./training-data"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def collect_scam_documents(self, min_confidence: int = 70) -> list[dict]:
        """Collect all confirmed scam documents from Qdrant."""
        from rugbrain.qdrant_store import get_qdrant_store

        store = await get_qdrant_store()
        docs = []
        for col in ["known_scams", "scam_patterns", "crime_reports", "defi_hacks"]:
            try:
                results = await store.search(
                    collection=col,
                    query_vector=[0.0] * 1024,
                    limit=1000,
                    with_payload=True,
                )
                for r in results:
                    metadata = r.get("payload", {}).get("metadata", {})
                    if metadata.get("confidence", 0) >= min_confidence:
                        docs.append(
                            {
                                "text": r.get("content", ""),
                                "label": "scam",
                                "collection": col,
                                "scam_type": metadata.get("scam_type", "unknown"),
                                "severity": metadata.get("severity", "medium"),
                                "chain": metadata.get("chain", "unknown"),
                            }
                        )
            except Exception as e:
                logger.warning("collect_scam_documents: %s failed: %s", col, e)
        return docs

    async def collect_legitimate_documents(self, limit: int = 1000) -> list[dict]:
        """Collect legitimate token/protocol documents as negatives."""
        from rugbrain.qdrant_store import get_qdrant_store

        store = await get_qdrant_store()
        docs = []
        for col in ["token_analysis", "wallet_profiles", "market_intel"]:
            try:
                results = await store.search(
                    collection=col,
                    query_vector=[0.0] * 1024,
                    limit=limit,
                    with_payload=True,
                )
                for r in results:
                    docs.append(
                        {
                            "text": r.get("content", ""),
                            "label": "legitimate",
                            "collection": col,
                        }
                    )
            except Exception as e:
                logger.warning("collect_legitimate: %s failed: %s", col, e)
        return docs[:limit]

    async def build_training_pairs(self) -> dict:
        """Build positive + hard-negative training pairs."""
        scams = await self.collect_scam_documents()
        legit = await self.collect_legitimate_documents()

        pairs = {"positive_pairs": [], "triplets": []}

        for i, scam_a in enumerate(scams):
            for j, scam_b in enumerate(scams):
                if i < j and scam_a["scam_type"] == scam_b["scam_type"]:
                    pairs["positive_pairs"].append(
                        {
                            "query": scam_a["text"][:500],
                            "positive": scam_b["text"][:500],
                        }
                    )
                    break

        for scam in scams[: len(legit)]:
            hard_neg = legit.pop(0)
            pairs["triplets"].append(
                {
                    "anchor": scam["text"][:500],
                    "positive": scams[len(scams) // 2]["text"][:500],
                    "negative": hard_neg["text"][:500],
                }
            )

        return pairs

    async def export_training_dataset(self) -> str:
        """Export training data as JSON for HuggingFace training."""
        pairs = await self.build_training_pairs()
        output_path = self.output_dir / "training_data.json"

        def _write_files() -> None:
            with open(output_path, "w") as f:
                json.dump(pairs, f, indent=2)

            # Also write a dataset card
            card_path = self.output_dir / "README.md"
            card = f"""# RugBrain — Crypto Scam Detection Training Dataset

- Positive pairs: {len(pairs["positive_pairs"])} confirmed scam document pairs
- Triplets: {len(pairs["triplets"])} anchor-positive-negative triplets
- Source: 20 RMI Qdrant collections from rugmunch.io
- Training target: bge-m3 fine-tuned for crypto scam detection
- License: Proprietary — Rug Munch Media LLC
"""
            with open(card_path, "w") as f:
                f.write(card)

        await asyncio.to_thread(_write_files)
        logger.info(
            "Training dataset exported to %s (%d pairs, %d triplets)",
            output_path,
            len(pairs["positive_pairs"]),
            len(pairs["triplets"]),
        )
        return str(output_path)


async def get_fine_tuner() -> EmbeddingTrainingData:
    return EmbeddingTrainingData()


__all__ = ["EmbeddingTrainingData", "get_fine_tuner"]
