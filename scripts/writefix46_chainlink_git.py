#!/usr/bin/env python3
"""Chainlink reference-first ingest when the GitHub API token is rejected.

The API path is refused (401); the repo's own git fallback exists for this.
Here the tree comes from the already-cloned mirror, content from raw
(raw.githubusercontent.com needs no token), chunking/classification from
``rugbrain.ingest_sources.repo_docs`` -- the production repo_docs rules.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import httpx

from rugbrain.ingest_sources.base import IngestDoc
from rugbrain.ingest_sources.repo_docs import (
    NOISE,
    _page_priority,
    chunk_sections,
    classify_page_kind,
    split_sections,
)
from rugbrain.qdrant_store import QdrantVectorStore
from ingest_all_sources import _store_docs

QURL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
REPO, REF, CLONE, CAP = "smartcontractkit/documentation", "5f4f60df974d", "/tmp/clg", 40


def tree() -> list[str]:
    out = subprocess.run(
        ["git", "-C", CLONE, "ls-tree", "-r", "--name-only", REF],
        check=True, capture_output=True, text=True,
    ).stdout
    return [
        p for p in out.splitlines()
        if p.lower().endswith((".md", ".mdx")) and not any(n in p.lower() for n in NOISE)
    ]


async def main() -> None:
    paths = sorted(
        tree(),
        key=lambda p: (0 if "/data-feeds/" in p else 1, _page_priority(p)),
    )[:CAP]
    docs: list[IngestDoc] = []
    kinds: Counter[str] = Counter()
    async with httpx.AsyncClient(timeout=60.0) as c:
        for path in paths:
            url = f"https://raw.githubusercontent.com/{REPO}/{REF}/{path}"
            r = await c.get(url)
            if r.status_code != 200 or not r.text.strip():
                continue
            sections = split_sections(r.text.replace("\r\n", "\n"))
            kind = classify_page_kind(path, sections[0][0] if sections else "")
            for title, chunk in chunk_sections(sections):
                docs.append(IngestDoc(
                    content=f"{REPO} @ {REF[:12]} — {path}\n\n{chunk}",
                    doc_id=f"repo_docs:{REPO}:{REF[:12]}:{path}:{abs(hash(chunk))}",
                    doc_type="docs", source="repo_docs", title=title or path, url=path,
                    metadata={"repo": REPO, "ref": REF, "path": path, "page_kind": kind,
                              "collection": "rag_documents_8b", "fetch": "git+raw"},
                ))
                kinds[kind] += 1
    print(json.dumps({
        "tree_files": len(tree()), "pages_selected": len(paths), "chunks": len(docs),
        "by_page_kind": dict(kinds),
        "data_feeds": [p for p in paths if "/data-feeds/" in p][:3],
    }, indent=2))
    store = QdrantVectorStore(QURL)
    async with httpx.AsyncClient(timeout=120.0) as embed_client:
        print(json.dumps(await _store_docs(store, embed_client, "repo_docs",
                                           "rag_documents_8b", docs, False), indent=2))
    await store.close()


asyncio.run(main())
