#!/usr/bin/env python3
"""Refresh the big-crypto-player corpus: manifest, bounded ingest, spot checks.

The "players" are the publishers that set the vocabulary the market reasons
with — a16z crypto, Flashbots, Chainalysis, plus the Base / Arbitrum /
DefiLlama docs. Their sources live in ``rugbrain.ingest_sources`` (see
``players_research.py`` / ``players_docs.py``); this script is the operator
side: one bounded batch through the canonical store path
(``scripts/ingest_all_sources.py`` — chunk, dedupe on deterministic ids, embed
on the 4096-d 8B lane, upsert), then retrieval spot checks.

The manifest is DERIVED from the registry + licence registry, never
hand-maintained, so publisher/licence/doc_type can only come from one place.

Usage:
    python scripts/refresh_corpus_players.py --write-manifest
    python scripts/refresh_corpus_players.py --limit 12
    python scripts/refresh_corpus_players.py --spot-check-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import httpx  # noqa: E402
from ingest_all_sources import run  # noqa: E402

from rugbrain.ingest_sources import registry  # noqa: E402
from rugbrain.ingest_sources.licenses import terms_for  # noqa: E402

#: The player sources this script owns. Order = manifest order.
PLAYER_SOURCES: tuple[str, ...] = (
    "a16zcrypto",
    "flashbots_writings",
    "chainalysis_insights",
    "base_docs",
    "arbitrum_docs",
    "defillama_docs",
)

MANIFEST_PATH = ROOT / "config" / "corpus_players_manifest.json"

#: (query, source) — the retrieval claim each source must be able to answer.
SPOT_CHECKS: tuple[tuple[str, str], ...] = (
    ("MEV supply chain builder relay block building", "flashbots_writings"),
    ("on-chain crime typology exchange deposit flows sanctions", "chainalysis_insights"),
    ("crypto regulation market structure policy Washington", "a16zcrypto"),
    ("Base network details mainnet RPC chain id", "base_docs"),
    ("Arbitrum bridge withdrawal finalisation Nitro", "arbitrum_docs"),
    ("how DefiLlama calculates TVL methodology", "defillama_docs"),
)


def build_manifest() -> list[dict[str, object]]:
    """Manifest rows derived from the registry + licence registry."""
    rows: list[dict[str, object]] = []
    for name in PLAYER_SOURCES:
        src = registry.get_source(name)
        if src is None:
            rows.append({"source": name, "missing": True})
            continue
        terms = terms_for(name)
        rows.append(
            {
                "source": name,
                "publisher": getattr(src, "publisher_name", ""),
                "doc_type": src.doc_type,
                "collection": src.default_collection,
                "canonical_id": "<source>:<slug>:<contenthash>",
                "license": terms.license,
                "permitted_use": terms.permitted_use,
                "chunkable": terms.chunkable,
                "redistribute": terms.redistribute,
                "description": src.description,
            }
        )
    return rows


def write_manifest(path: Path) -> int:
    """Write the derived manifest; returns the row count."""
    rows = build_manifest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sources": rows}, indent=2) + "\n")
    return len(rows)


async def spot_check(limit: int = 3, min_similarity: float = 0.25) -> dict[str, object]:
    """Embed each query and search ITS source's collection, filtered by source."""
    from embed_lane import embed_batch

    from rugbrain.qdrant_store import QdrantVectorStore

    results: dict[str, object] = {}
    async with httpx.AsyncClient(timeout=120.0) as client:
        store = QdrantVectorStore()
        try:
            for query, source in SPOT_CHECKS:
                coll = registry.get_source(source).default_collection
                try:
                    vectors = await embed_batch([query], client=client)
                    vector = vectors[0] if vectors else None
                except Exception as e:
                    results[source] = {"query": query, "error": f"embed: {type(e).__name__}"}
                    continue
                if not vector:
                    results[source] = {"query": query, "error": "embed_empty"}
                    continue
                hits = await store.search(
                    vector,
                    collection=coll,
                    limit=limit,
                    min_similarity=min_similarity,
                    filters={"source": source},
                )
                results[source] = {
                    "query": query,
                    "collection": coll,
                    "hits": len(hits),
                    "top": [
                        {
                            "score": round(float(h.get("score") or 0), 3),
                            "title": str((h.get("metadata") or {}).get("title", ""))[:60],
                            "url": str((h.get("metadata") or {}).get("source_url", ""))[:80],
                        }
                        for h in hits
                    ],
                }
        finally:
            await store.close()
    return results


async def refresh(limit: int | None) -> dict[str, object]:
    """One bounded batch over every player source, using the canonical runner."""
    per_source: dict[str, object] = {}
    for name in PLAYER_SOURCES:
        try:
            totals = await run(name, limit, dry_run=False)
            per_source[name] = totals.get("per_source", {}).get(name, totals)
        except Exception as e:  # a dead source never aborts the batch
            per_source[name] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    return per_source


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the big-crypto-player corpus")
    parser.add_argument("--limit", type=int, default=12, help="max docs per source")
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--spot-check-only", action="store_true")
    args = parser.parse_args()

    if args.write_manifest:
        n = write_manifest(MANIFEST_PATH)
        print(f"manifest written: {MANIFEST_PATH} sources={n}")

    if not args.spot_check_only:
        per_source = asyncio.run(refresh(args.limit))
        print(json.dumps(per_source, indent=2, default=str))

    checks = asyncio.run(spot_check())
    print(json.dumps(checks, indent=2, default=str))
    misses = [s for s, v in checks.items() if not (v or {}).get("hits")]
    print(f"spot_checks: {len(checks) - len(misses)}/{len(checks)} returned hits")
    if misses:
        print(f"no hits: {', '.join(misses)}")
    return 0 if not misses else 1


if __name__ == "__main__":
    raise SystemExit(main())
