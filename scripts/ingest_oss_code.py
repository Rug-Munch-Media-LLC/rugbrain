#!/usr/bin/env python3
"""Bounded OSS source ingest into Qdrant collection `oss_code` (4096-d)."""
import json
import os
import pathlib
import re
import subprocess
import sys
import uuid

import httpx

QDRANT = "http://127.0.0.1:6334"
COL = "oss_code"
ENV = "/srv/work/repos/rugbrain/.env.rag"
ROOT = pathlib.Path("/srv/work/oss-src")
PER_REPO_FILES = 400
MAX_CHUNKS = 6000
CHUNK_LINES, OVERLAP = 150, 10
EXTS = {".sol", ".rs", ".ts", ".js", ".mjs", ".cjs", ".py", ".go", ".move", ".cairo", ".vy", ".tsx", ".jsx"}
SKIP_DIRS = {".git", "node_modules", "target", "out", "artifacts", "cache", "dist", "build", "coverage",
             "test", "tests", "__tests__", "bench", "benches", "examples", "example", "docs", "doc", "fixtures"}
REPOS = [
    "OpenZeppelin/openzeppelin-contracts", "solana-labs/solana-program-library",
    "Uniswap/v3-core", "Uniswap/v4-core", "aave/aave-v3-core",
    "foundry-rs/foundry", "wevm/viem", "coral-xyz/anchor",
]
LANG = {".sol": "solidity", ".rs": "rust", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
        ".mjs": "javascript", ".cjs": "javascript", ".py": "python", ".go": "go", ".move": "move",
        ".cairo": "cairo", ".vy": "vyper"}
PERMISSIVE = ("MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MPL-2.0", "Unlicense",
              "GPL-2.0-only", "GPL-3.0-only", "GPL-3.0-or-later", "LGPL-3.0-only", "AGPL-3.0-only")

def env_key():
    if not os.path.exists(ENV):
        return None, None
    kv = {}
    for line in open(ENV, encoding="utf-8", errors="ignore"):
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.+)", line)
        if m:
            kv[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return (kv.get("SILICONFLOW_API_KEY") or kv.get("SILICONFLOW_KEY"),
            kv.get("SILICONFLOW_BASE_URL") or "https://api.siliconflow.com/v1")

def spdx(text):
    m = re.search(r"SPDX-License-Identifier:\s*([A-Za-z0-9.\-+]+)", text)
    if m:
        return m.group(1)
    t = text.lower()
    if "apache license" in t and "2.0" in t: return "Apache-2.0"
    if "business source license" in t: return "BUSL-1.1"
    if "gnu affero general public license" in t: return "AGPL-3.0-only"
    if "gnu lesser general public license" in t: return "LGPL-3.0-only"
    if "gnu general public license" in t:
        return "GPL-3.0-or-later" if "any later version" in t else ("GPL-3.0-only" if "version 3" in t else "GPL-2.0-only")
    if "mozilla public license" in t: return "MPL-2.0"
    if "redistribution and use in source and binary forms" in t:
        return "BSD-3-Clause" if "neither the name" in t else "BSD-2-Clause"
    if "permission to use, copy, modify, and/or distribute" in t: return "ISC"
    if "this is free and unencumbered software" in t: return "Unlicense"
    if "permission is hereby granted, free of charge" in t: return "MIT"
    return "UNKNOWN"

def licence_of(d):
    for pat in ("LICEN[CS]E*", "COPYING*", "LICENCE*"):
        for p in sorted(d.glob(pat)):
            if p.is_file() and p.stat().st_size < 60000:
                return spdx(p.read_text(encoding="utf-8", errors="ignore"))
    return "UNKNOWN"

def clone(repo):
    dest = ROOT / repo.replace("/", "__")
    if not (dest / ".git").exists():
        subprocess.run(["git", "clone", "--depth", "1", "--single-branch",
                        f"https://github.com/{repo}.git", str(dest)],
                       check=True, capture_output=True, timeout=900)
    sha = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return dest, sha

def files_of(d):
    out = []
    for p in d.rglob("*"):
        if not p.is_file() or p.suffix not in EXTS:
            continue
        rel = p.relative_to(d)
        if any(part.lower() in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if p.name.endswith(".min.js") or p.stat().st_size > 200_000:
            continue
        out.append(p)
    return sorted(out)[:PER_REPO_FILES]

def chunks(path):
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    if len(lines) < 3:
        return []
    out, start = [], 0
    while start < len(lines):
        end = min(start + CHUNK_LINES, len(lines))
        body = "\n".join(lines[start:end]).strip()
        if len(body) > 40:
            out.append((start + 1, end, body[:4000]))
        if end == len(lines):
            break
        start = max(end - OVERLAP, start + 1)
    return out

def main():
    key, base = env_key()
    hc = httpx.Client(timeout=180)
    try:
        hc.put(f"{QDRANT}/collections/{COL}", json={"vectors": {"size": 4096, "distance": "Cosine"}})
    except Exception:
        pass
    info = hc.get(f"{QDRANT}/collections/{COL}").json().get("result") or {}
    size = (info.get("config", {}).get("params", {}).get("vectors") or {}).get("size")
    if size != 4096:
        sys.exit(f"BLOCKED: {COL} dim={size}, need 4096")
    if key:
        emb_url, emb_key, model = f"{base}/embeddings", key, "Qwen/Qwen3-Embedding-8B"
    else:
        emb_url, emb_key = "http://127.0.0.1:8024/v1/embeddings", "local"
        model = hc.get("http://127.0.0.1:8024/v1/models").json()["data"][0]["id"]
    print(f"LANE {emb_url} model={model} key={'sf' if key else 'local'}")

    def embed(texts):
        r = hc.post(emb_url, headers={"Authorization": f"Bearer {emb_key}"},
                    json={"model": model, "input": texts})
        r.raise_for_status()
        v = [d["embedding"] for d in r.json()["data"]]
        assert len(v[0]) == 4096, f"dim {len(v[0])}"
        return v

    summary, total = [], 0
    for repo in REPOS:
        try:
            d, sha = clone(repo)
        except Exception as e:
            summary.append({"repo": repo, "error": f"clone failed: {str(e)[:120]}"}); print("SKIP", repo, e); continue
        lic = licence_of(d)
        files = files_of(d)
        pts = []
        for p in files:
            rel = str(p.relative_to(d))
            for s, e, body in chunks(p):
                doc = f"{repo} {rel}:L{s}-L{e}\n{body}"
                pts.append({"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{repo}@{sha}:{rel}:{s}-{e}")),
                            "payload": {"repo": repo, "path": rel, "commit": sha, "licence": lic,
                                        "language": LANG.get(p.suffix, "text"), "line_range": f"L{s}-L{e}",
                                        "start_line": s, "end_line": e,
                                        "publishable": lic in PERMISSIVE,
                                        "source_url": f"https://github.com/{repo}/blob/{sha}/{rel}#L{s}-L{e}",
                                        "title": f"{repo}/{rel}:{s}-{e}", "content": body}})
            if total + len(pts) > MAX_CHUNKS:
                break
        vecs = []
        for i in range(0, len(pts), 32):
            vecs.extend(embed([p["payload"]["content"] for p in pts[i:i + 32]]))
        for p, v in zip(pts, vecs):
            p["vector"] = v
        for i in range(0, len(pts), 64):
            hc.put(f"{QDRANT}/collections/{COL}/points?wait=true", json={"points": pts[i:i + 64]}).raise_for_status()
        total += len(pts)
        summary.append({"repo": repo, "licence": lic, "commit": sha, "files": len(files),
                        "chunks": len(pts), "publishable": lic in PERMISSIVE})
        print(f"OK {repo:<42} {lic:<16} {sha[:12]} files={len(files):<4} chunks={len(pts)}")

    proofs = ["how does viem encode a transaction", "how does SPL Token-2022 transfer hook work",
              "how does OpenZeppelin ERC4626 round shares"]
    for q in proofs:
        v = embed([q])[0]
        r = hc.post(f"{QDRANT}/collections/{COL}/points/search",
                    json={"vector": v, "limit": 3, "with_payload": True}).json()["result"]
        print(f"\nPROOF: {q}")
        for h in r:
            pl = h["payload"]
            print(f"  {pl['source_url']}  score={h['score']:.4f}")
            print("    " + pl["content"].splitlines()[0][:110])
    json.dump(summary, open("/tmp/opencode/oss_ingest.json", "w"), indent=1)
    print("\nSUMMARY", json.dumps(summary)[:1800])

if __name__ == "__main__":
    main()
