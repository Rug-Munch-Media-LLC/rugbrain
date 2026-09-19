"""Chain-data firehose sources — etherscan labels, solana scams, patterns.

These stay inline (no dedicated upstream module) as module-level functions
taking an ``httpx.AsyncClient``; ``FeedSources`` delegates to them.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

import aiofiles
import httpx

logger = logging.getLogger("rag.firehose")

__all__ = ["extract_scam_patterns", "pull_etherscan_labels", "pull_solana_scams"]


async def pull_etherscan_labels(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Pull etherscan labeled addresses (via existing CSV)."""
    del client  # unused; kept for FeedSources signature symmetry
    docs: list[dict[str, Any]] = []
    # __file__ is rugbrain/firehose/chain_sources.py -> go up 2 levels to rugbrain/../data/
    csv_rel = os.path.join(
        os.path.dirname(__file__), "..", "..", "data", "etherscan_phish_hack.csv"
    )
    csv_path = await asyncio.get_running_loop().run_in_executor(None, os.path.abspath, csv_rel)
    # Fall back to rugbrain/../data/ (original firehose.py location)
    if not await asyncio.get_running_loop().run_in_executor(None, os.path.exists, csv_path):
        csv_fallback = os.path.join(
            os.path.dirname(__file__), "..", "data", "etherscan_phish_hack.csv"
        )
        csv_path = await asyncio.get_running_loop().run_in_executor(
            None, os.path.abspath, csv_fallback
        )

    if not await asyncio.get_running_loop().run_in_executor(None, os.path.exists, csv_path):
        return docs

    import csv

    try:
        async with aiofiles.open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda fh: csv.DictReader(fh),  # type: ignore[call-overload]
                    f,
                )
            )

        for row in rows:
            addr = row.get("address", "").strip()
            if not addr:
                continue
            content = (
                f"Etherscan label: {addr} on {row.get('chain', 'Ethereum')}. "
                f"Tag: {row.get('name_tag', '')}. Type: {row.get('label_type', 'scam')}."
            )
            docs.append(
                {
                    "collection": "known_scams",
                    "content": content,
                    "metadata": {
                        "address": addr.lower(),
                        "chain": (row.get("chain", "Ethereum") or "Ethereum").lower(),
                        "label_type": row.get("label_type", "scam"),
                        "source": "etherscan",
                        "severity": "critical"
                        if "phish" in row.get("label_type", "").lower()
                        else "high",
                    },
                }
            )
    except Exception as e:
        logger.warning("Etherscan pull failed: %s", e)

    return docs


async def pull_solana_scams(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Pull Solana token registry flagged tokens."""
    docs: list[dict[str, Any]] = []
    try:
        resp = await client.get(
            "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json",
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            for token in data.get("tokens", []):
                tags = [t.lower() for t in token.get("tags", [])]
                if any(kw in str(tags) for kw in ["scam", "spam", "fake"]):
                    docs.append(
                        {
                            "collection": "known_scams",
                            "content": f"Solana scam token: {token.get('name', '')} "
                            f"({token.get('symbol', '')}) "
                            f"at {token.get('address', '')}. Tags: {tags}.",
                            "metadata": {
                                "address": token.get("address", ""),
                                "name": token.get("name", ""),
                                "symbol": token.get("symbol", ""),
                                "chain": "solana",
                                "source": "solana_token_registry",
                                "tags": tags,
                                "severity": "high",
                            },
                        }
                    )
    except Exception as e:
        logger.warning("Solana pull failed: %s", e)

    return docs


async def extract_scam_patterns(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Extract common patterns from confirmed scam documents.

    Searches the LOCAL Qdrant ``known_scams`` collection directly — the
    former backend search call is gone (standalone, §5.2).
    """
    del client  # unused; kept for FeedSources signature symmetry
    docs: list[dict[str, Any]] = []
    try:
        from rugbrain.embeddings import get_query_embedding
        from rugbrain.qdrant_store import get_qdrant_store

        embedding = await get_query_embedding("rug pull honeypot scam confirmed")
        store = await get_qdrant_store()
        results = await store.search(embedding, collection="known_scams", limit=50)
        if len(results) >= 10:
            content_parts = [r.get("content", "")[:200] for r in results[:20]]
            content_parts = [c for c in content_parts if c]

            combined = "Common scam patterns observed: " + " | ".join(content_parts)
            docs.append(
                {
                    "collection": "scam_patterns",
                    "content": combined[:5000],
                    "metadata": {
                        "source": "pattern_extraction",
                        "pattern_count": len(results),
                        "extracted_at": datetime.now(UTC).isoformat(),
                    },
                }
            )
    except Exception as e:
        logger.warning("Pattern extraction failed: %s", e)

    return docs
