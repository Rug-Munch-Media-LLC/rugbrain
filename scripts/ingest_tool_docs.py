#!/usr/bin/env python3
"""Ingest docs for the tools we RUN, from shallow pinned clones (no GitHub API).

Pinned by commit sha; licence read from the repo at that sha; reference pages first.
"""
from __future__ import annotations
import asyncio, hashlib, json, os, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import httpx
from rugbrain.ingest_common import store_documents
from rugbrain.ingest_sources.base import IngestDoc, to_crawl_document
from rugbrain.ingest_sources.repo_docs import (_FRONTMATTER_RE, NOISE, classify_page_kind,
                                               chunk_sections, detect_licence, split_sections)
from rugbrain.qdrant_store import QdrantVectorStore
from rugbrain.qwen_embedders import Qwen3Embedder

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".toolsrc")
SOURCES = [
    ("modelcontextprotocol/modelcontextprotocol", ("docs/", "schema/"), (".md", ".mdx"), 70),
    ("ggml-org/llama.cpp", ("docs/", "tools/server/", "examples/"), (".md",), 60),
    ("qdrant/qdrant", ("docs/",), (".md",), 70),
    ("oraios/serena", ("docs/",), (".md",), 70),
    ("sourcegraph/zoekt", ("doc/",), (".md",), 40),
]

def clone(repo):
    dst = os.path.join(ROOT, repo.replace("/", "__"))
    if not os.path.isdir(os.path.join(dst, ".git")):
        subprocess.run(["git", "clone", "--depth", "1", "--single-branch", "-q",
                        f"https://github.com/{repo}.git", dst], check=True, timeout=600)
    sha = subprocess.run(["git", "-C", dst, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    return dst, sha

def licence(dst):
    for name in sorted(os.listdir(dst)):
        if name.upper().startswith(("LICENSE", "LICENCE", "COPYING")):
            with open(os.path.join(dst, name), encoding="utf-8", errors="replace") as fh:
                return detect_licence(fh.read()), name
    return "", ""

def pages(dst, prefixes, exts, cap):
    out = []
    for base, _dirs, files in os.walk(dst):
        if "/.git" in base:
            continue
        rel_dir = os.path.relpath(base, dst).replace(os.sep, "/")
        for f in files:
            rel = (f if rel_dir == "." else f"{rel_dir}/{f}")
            if not rel.lower().endswith(exts) or not rel.startswith(prefixes):
                continue
            if any(n in rel.lower() for n in NOISE):
                continue
            out.append(rel)
    return sorted(out, key=lambda p: ({"reference": 0, "guide": 1, "landing": 2}.get(classify_page_kind(p), 3), p))[:cap]

def docs_for(repo, dst, sha, prefixes, exts, cap):
    spdx, lpath = licence(dst)
    pub = spdx.upper() not in {"", "UNKNOWN", "NOASSERTION", "UNLICENSED", "PROPRIETARY"}
    docs, kinds = [], {}
    for rel in pages(dst, prefixes, exts, cap):
        with open(os.path.join(dst, rel), encoding="utf-8", errors="replace") as fh:
            body = _FRONTMATTER_RE.sub("", fh.read().replace("\r\n", "\n"), count=1).strip("\n")
        secs = split_sections(body)
        kind = classify_page_kind(rel, secs[0][0] if secs else "")
        for title, chunk in chunk_sections(secs):
            docs.append(IngestDoc(
                content=f"{repo} @ {sha[:12]} — {rel}\nLicence: {spdx or 'unknown'}\n\n{chunk}",
                doc_id=f"repo_docs:{repo}:{sha[:12]}:{rel}:{hashlib.sha256(chunk.encode()).hexdigest()[:16]}",
                doc_type="repo_documentation", source="repo_docs", title=title or rel, url=rel,
                metadata={"repo": repo, "ref": sha, "path": rel, "section": title, "licence": spdx,
                          "page_kind": kind, "publishable": pub, "collection": "repo_docs",
                          "licence_path": lpath}))
            kinds[kind] = kinds.get(kind, 0) + 1
    return docs, {"licence": spdx, "licence_path": lpath, "publishable": pub,
                  "by_page_kind": kinds, "reference_pages": kinds.get("reference", 0)}

async def proofs(emb, st):
    qs = [("mcp_streamable_http", "Streamable HTTP transport handshake Mcp-Session-Id initialization"),
          ("jsonrpc_tool_error", "JSON-RPC error object tool call result isError"),
          ("llamacpp_embed_flags", "llama-server embedding flags --embedding --pooling"),
          ("qdrant_named_vector", "query named vector name parameter dense"),
          ("serena_symbols", "find_referencing_symbols versus find_symbol")]
    res = {}
    for key, q in qs:
        vec = emb.embed_query(q)
        if hasattr(vec, "__await__"):
            vec = await vec
        hits, used = None, ""
        for label, call in (("search", lambda: st.search(vec, limit=3)), ("query", lambda: st.query(vec, limit=3)),
                            ("search_kw", lambda: st.search(vector=vec, limit=3))):
            try:
                hits, used = call(), label
                break
            except Exception as e:
                used = f"{label}:{type(e).__name__}"
        if hasattr(hits, "__await__"):
            hits = await hits
        rows = []
        for h in (hits or [])[:3]:
            md = getattr(h, "payload", None) or getattr(h, "metadata", None) or (h.get("payload") if isinstance(h, dict) else {}) or {}
            rows.append({"repo": md.get("repo"), "path": md.get("path"), "section": md.get("section"),
                         "licence": md.get("licence"), "score": getattr(h, "score", None)})
        res[key] = {"api": used, "hits": rows}
    return res

async def main(dry):
    out, all_docs = {"sources": {}}, []
    for repo, prefixes, exts, cap in SOURCES:
        try:
            dst, sha = clone(repo)
            docs, meta = docs_for(repo, dst, sha, prefixes, exts, cap)
        except Exception as e:
            out["sources"][repo] = {"error": f"{type(e).__name__}: {str(e)[:110]}"}
            continue
        out["sources"][repo] = {"ref": sha, "ref12": sha[:12], "docs": len(docs), **meta}
        all_docs.extend(docs)
    out["docs"] = len(all_docs)
    if dry or not all_docs:
        print(json.dumps(out, indent=2, default=str))
        return
    h = httpx.get(f"{QDRANT}/collections/repo_docs", timeout=20)
    if h.status_code == 404:
        httpx.put(f"{QDRANT}/collections/repo_docs", timeout=60,
                  json={"vectors": {"size": 4096, "distance": "Cosine"}})
    emb, st = Qwen3Embedder(provider=os.getenv("EMBED_PROVIDER", "siliconflow")), QdrantVectorStore(QDRANT)
    out["stored"] = await store_documents([to_crawl_document(d) for d in all_docs], emb, st,
                                          chunk_size=1500, chunk_overlap=100)
    out["proofs"] = await proofs(emb, st)
    print(json.dumps(out, indent=2, default=str))

if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
