#!/usr/bin/env python3
"""Bounded ingest of mechanism-level GitHub corpora with per-doc provenance.

Writes into the 4096-dim 8B lane collection ``github_audits`` (physical
``github_audits_8b``) — same model/dimension as ``contract_audits_8b`` and
``exploits_8b``, so no embedding space is mixed.

Every document carries: repo, path, commit (tree sha), licence (SPDX from the
GitHub API, allowlist-gated) and a permalink pinned to the commit. Sources whose
licence is not on the allowlist are SKIPPED, not copied.

Usage:
  python scripts/ingest_github_mechanisms.py --dry-run
  python scripts/ingest_github_mechanisms.py --total 300
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import httpx

from rugbrain.ingest_common import store_documents
from rugbrain.ingest_sources.base import IngestDoc, to_crawl_document
from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.qwen_embedders import Qwen3Embedder

COLLECTION = "github_audits"
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
ALLOW = {
    "Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause",
    "CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0",
}
SOURCES = [
    {"repo": "SunWeb3Sec/DeFiHackLabs", "ext": (".sol",), "prefix": ("src/test/",), "cap": 150},
    {"repo": "crytic/not-so-smart-contracts", "ext": (".md", ".sol"), "prefix": (), "cap": 100},
    {"repo": "immunefi-team/Web3-Security-Library", "ext": (".md",), "prefix": (), "cap": 50},
    {"repo": "OpenZeppelin/openzeppelin-contracts", "ext": (".md",), "prefix": ("audits/",), "cap": 60},
    {"repo": "transmissions11/solcurity", "ext": (".md",), "prefix": (), "cap": 40},
]


def _api(client: httpx.Client, path: str) -> dict:
    r = client.get(path, headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    return r.json()


def _licence(client: httpx.Client, repo: str) -> tuple[str, int]:
    meta = _api(client, f"https://api.github.com/repos/{repo}")
    spdx = (meta.get("license") or {}).get("spdx_id") or "NONE"
    return spdx, int(meta.get("stargazers_count") or 0)


def _files(client: httpx.Client, repo: str, spec: dict) -> tuple[str, list[str]]:
    tree = _api(client, f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1")
    sha = tree["sha"]
    paths = [
        n["path"] for n in tree.get("tree", [])
        if n.get("type") == "blob" and n["path"].endswith(spec["ext"])
        and (not spec["prefix"] or n["path"].startswith(spec["prefix"]))
    ]
    paths.sort(key=lambda p: -int(p.endswith(".sol")))
    return sha, paths[: spec["cap"]]


def _doc(repo: str, path: str, sha: str, lic: str, body: str, stars: int) -> IngestDoc:
    url = f"https://github.com/{repo}/blob/{sha}/{path}"
    head = f"// SPDX-License-Identifier: {lic}\n// Source: {repo}/{path} @ {sha[:12]}\n" \
        if path.endswith(".sol") else f"<!-- SPDX-License-Identifier: {lic} | {repo}/{path} @ {sha[:12]} -->\n"
    return IngestDoc(
        content=head + body,
        doc_id="gh1:" + hashlib.sha1(f"{repo}:{path}:{sha}".encode()).hexdigest()[:24],
        metadata={"repo": repo, "path": path, "commit": sha, "licence": lic,
                  "collection": COLLECTION, "stars": stars, "doc_type": "mechanism"},
        doc_type="mechanism",
        source="github_mechanisms",
        title=f"{repo.rsplit('/', 1)[-1]}:{path.rsplit('/', 1)[-1]}",
        url=url,
    )


def collect(total: int, dry_run: bool) -> dict:
    summary = {"sources": {}, "docs": 0}
    docs: list[IngestDoc] = []
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for spec in SOURCES:
            repo = spec["repo"]
            try:
                lic, stars = _licence(client, repo)
            except Exception as e:  # fail-open, never abort the batch
                summary["sources"][repo] = {"error": str(e)[:120]}
                continue
            if lic not in ALLOW:
                summary["sources"][repo] = {"licence": lic, "skipped": "licence not allowlisted"}
                continue
            if len(docs) >= total:
                break
            try:
                sha, paths = _files(client, repo, spec)
            except Exception as e:
                summary["sources"][repo] = {"licence": lic, "error": str(e)[:120]}
                continue
            got = 0
            for p in paths:
                if len(docs) >= total or got >= spec["cap"]:
                    break
                try:
                    raw = client.get(f"https://raw.githubusercontent.com/{repo}/{sha}/{p}")
                    raw.raise_for_status()
                except Exception:
                    continue
                body = raw.text.strip()
                if len(body) < 400:
                    continue
                docs.append(_doc(repo, p, sha, lic, body[:20000], stars))
                got += 1
            summary["sources"][repo] = {"licence": lic, "stars": stars,
                                        "commit": sha[:12], "files": len(paths), "docs": got}
    summary["docs"] = len(docs)
    if dry_run or not docs:
        return summary
    embedder = Qwen3Embedder(provider=os.getenv("EMBED_PROVIDER", "siliconflow"))
    store = QdrantVectorStore(QDRANT_URL)
    summary["stored"] = asyncio.run(store_documents(
        [to_crawl_document(d) for d in docs], embedder, store, chunk_size=1500, chunk_overlap=100)
    )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    print(json.dumps(collect(args.total, args.dry_run), indent=2, default=str))


if __name__ == "__main__":
    main()
