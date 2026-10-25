#!/usr/bin/env python3
"""writefix46 verification: real ingests through the ONE client contract.

(a) gamblefi is run by scripts/ingest_gamblefi_docs.py (production path).
(b)(c) here: registered social pages -> rag_documents_8b, and Chainlink
``smartcontractkit/documentation`` via repo_docs, reference-first.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import httpx

from rugbrain.ingest_sources.base import IngestDoc
from rugbrain.ingest_sources.devdocs_specs_social import SOCIAL_SITES
from rugbrain.ingest_sources.repo_docs import RepoDocsSource, collect
from rugbrain.qdrant_store import QdrantVectorStore
from ingest_all_sources import _store_docs

QURL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
CHAINLINK_REPO = "smartcontractkit/documentation"
CHAINLINK_REF = "5f4f60df974d"
HEADERS = {"User-Agent": "rugbrain-writefix46/1.0"}


async def points(collection: str) -> int | None:
    async with httpx.AsyncClient(base_url=QURL, timeout=15.0) as c:
        r = await c.get(f"/collections/{collection}")
        return None if r.status_code != 200 else int(r.json()["result"].get("points_count") or 0)


async def social_docs(limit: int = 17) -> list[IngestDoc]:
    """Fetch the registered social reference pages (pinned URLs when present)."""
    urls: list[str] = []
    for site in SOCIAL_SITES:
        for u in getattr(site, "urls", ()) or (getattr(site, "url", ""),):
            if isinstance(u, str) and u.startswith("http") and u not in urls:
                urls.append(u)
    docs: list[IngestDoc] = []
    async with httpx.AsyncClient(timeout=60.0, headers=HEADERS) as c:
        for url in urls:
            if len(docs) >= limit:
                break
            try:
                r = await c.get(url)
            except Exception:
                continue
            if r.status_code != 200 or not r.text.strip():
                continue
            docs.append(
                IngestDoc(
                    content=r.text,
                    doc_id=f"social:{url}",
                    doc_type="docs",
                    source="devdocs_social",
                    title=url,
                    url=url,
                    metadata={"collection": "rag_documents_8b", "page_kind": "social_reference"},
                )
            )
    return docs


async def chainlink() -> tuple[list[IngestDoc], dict]:
    spec = RepoDocsSource(
        CHAINLINK_REPO, CHAINLINK_REF, collection="rag_documents_8b", max_pages=40
    )
    async with httpx.AsyncClient(timeout=120.0, headers=HEADERS) as c:
        return await collect(c, spec, limit=40)


async def main() -> None:
    out: dict = {"before": {c: await points(c) for c in ("gamblefi_docs", "rag_documents_8b")}}
    store = QdrantVectorStore(QURL)
    async with httpx.AsyncClient(timeout=120.0) as embed_client:
        social = await social_docs(17)
        out["social"] = {
            "docs": len(social),
            "store": await _store_docs(
                store, embed_client, "devdocs_social", "rag_documents_8b", social, False
            ),
        }
        docs, stats = await chainlink()
        out["chainlink"] = {
            "docs": len(docs),
            "collect": stats,
            "store": await _store_docs(
                store, embed_client, "repo_docs", "rag_documents_8b", docs, False
            ),
            "data_feeds": sorted(
                {
                    str(d.metadata.get("path"))
                    for d in docs
                    if "data-feeds/" in str(d.metadata.get("path", ""))
                }
            )[:5],
        }
    await store.close()
    out["after"] = {c: await points(c) for c in ("gamblefi_docs", "rag_documents_8b")}
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
