#!/usr/bin/env python3
"""Three retrieval spot checks over the ingested bot/launchpad docs.

Proves the WRITE and the READ path agree: embed the query on the 4096-d 8B
lane, dense-search ``research_v2`` (routed to ``research_v2_8b``), then rerank
the candidate pool on the local Qwen3-Reranker-8B lane (``:8003``). A hit that
survives the rerank and carries a ``canonical_id`` from
``config/bots_launchpads_manifest.json`` is the evidence that the batch landed.

Read-only: GET/search only, no writes.

Usage:
    python scripts/check_bots_launchpads.py
    python scripts/check_bots_launchpads.py --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.qwen_embedders import Qwen3Embedder
from rugbrain.rerank_endpoint import rerank_endpoint

COLLECTION = "research_v2"
RERANK_URL = os.getenv("CONTENT_RERANK_URL", "http://127.0.0.1:8003")
RERANK_MODEL = os.getenv("CONTENT_RERANK_MODEL", "qwen3-reranker-8b")
QUERIES = (
    "pump.fun bonding curve, fees and graduation mechanics",
    "frozen or restricted token how to appeal in Miss Rose",
    "x402 HTTP 402 payment header for agent payments on Base",
)


async def _rerank(query: str, hits: list[dict], top: int) -> list[dict]:
    """Rerank hits on the local 8B lane; return hits in the new order."""
    payload = {
        "model": RERANK_MODEL,
        "query": query,
        "documents": [str(h.get("content", ""))[:4000] for h in hits],
        "top_n": len(hits),
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(rerank_endpoint(RERANK_URL), json=payload)
    if resp.status_code != 200:
        return hits[:top]
    scores = {int(r["index"]): float(r.get("relevance_score", 0.0)) for r in resp.json().get("results", [])}
    scored = [(float(scores.get(i, 0.0)), h) for i, h in enumerate(hits)]
    scored.sort(key=lambda pair: -pair[0])
    for score, hit in scored[:top]:
        hit["rerank_score"] = round(score, 4)
    return [h for _, h in scored[:top]]


async def main_async(as_json: bool) -> int:
    """Run the three spot checks and print the surviving chunks."""
    embedder = Qwen3Embedder()
    if int(getattr(embedder, "dims", 0)) != 4096:
        print(json.dumps({"error": "embedder_not_4096"}) if as_json else "embedder not 4096")
        return 2
    store = QdrantVectorStore(url=os.getenv("QDRANT_URL", "http://127.0.0.1:6334"))
    report = []
    for query in QUERIES:
        vec = await embedder.embed_one(query)
        hits = await store.search(vec, collection=COLLECTION, limit=12, min_similarity=0.0)
        ranked = await _rerank(query, hits, 3) if hits else []
        report.append(
            {
                "query": query,
                "dense_hits": len(hits),
                "top": [
                    {
                        "score": round(float(h.get("score", 0)), 4),
                        "rerank_score": h.get("rerank_score"),
                        "title": str((h.get("metadata") or {}).get("title", ""))[:90],
                        "canonical_id": (h.get("metadata") or {}).get("canonical_id", ""),
                        "publisher": (h.get("metadata") or {}).get("publisher", ""),
                    }
                    for h in ranked
                ],
            }
        )
    if as_json:
        print(json.dumps(report, indent=2))
        return 0
    for r in report:
        print(f"\nQ: {r['query']}  (dense hits: {r['dense_hits']})")
        for i, t in enumerate(r["top"], 1):
            print(f"  {i}. [{t['rerank_score']}] {t['publisher']} | {t['title']} | {t['canonical_id']}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    sys.exit(asyncio.run(main_async(ap.parse_args().json)))
