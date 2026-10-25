#!/usr/bin/env python3
"""Embed + upsert EIP/ERC and Solana doc chunks into dedicated publishable Qdrant collections.

Evidence over claims: prints the embed dim, per-source counts, and a before/after proof
block for four standards questions. --budget stops embedding on wall clock and leaves a
resumable state file, so a partial run is visible rather than reported as complete.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from parse import detect_licence, eip_records, sol_records

QDRANT = "http://127.0.0.1:6333"
EMBED = "http://127.0.0.1:8024/v1/embeddings"
EIPS_COLL, SOL_COLL = "standards_eips", "standards_solana"
EIPS_SHA = "7372d10a457b29eceb02cd052345af165de1fb60"
QUERIES = [
    ("what does EIP-7702 allow", EIPS_COLL),
    ("ERC-4626 rounding direction", EIPS_COLL),
    ("how do Solana PDAs derive", SOL_COLL),
    ("what is a Solana transfer hook", SOL_COLL),
]


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    key = os.environ.get("EMBED_API_KEY", "")
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def req(method: str, url: str, payload: Any = None, timeout: int = 120) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=_headers())
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def embed(texts: list[str]) -> list[list[float]]:
    out = req("POST", EMBED, {"input": texts})
    return [d["embedding"] for d in out["data"]]


def ensure(coll: str, dim: int) -> str:
    try:
        req("GET", f"{QDRANT}/collections/{coll}")
        return "exists"
    except urllib.error.HTTPError:
        req("PUT", f"{QDRANT}/collections/{coll}", {"vectors": {"size": dim, "distance": "Cosine"}})
        for field in ("doc_type", "licence", "eip_number", "publishable", "section"):
            try:
                req("PUT", f"{QDRANT}/collections/{coll}/index", {"field_name": field, "field_schema": "keyword"})
            except urllib.error.HTTPError as exc:
                print("index-skip", field, exc.code)
        return "created"


def search(coll: str, vec: list[float], limit: int = 3) -> list[dict]:
    try:
        r = req("POST", f"{QDRANT}/collections/{coll}/points/query",
                {"query": vec, "limit": limit, "with_payload": True})
        return r["result"]["points"]
    except urllib.error.HTTPError:
        r = req("POST", f"{QDRANT}/collections/{coll}/points/search",
                {"vector": vec, "limit": limit, "with_payload": True})
        return r["result"]


def proof_block(tag: str, limit: int = 2) -> list[dict]:
    rows = []
    print(f"===== {tag} =====")
    for q, coll in QUERIES:
        try:
            hits = search(coll, embed([q])[0], limit)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            rows.append({"q": q, "collection": coll, "error": str(exc)[:120]})
            print(json.dumps(rows[-1]))
            continue
        top = hits[0]["payload"] if hits else {}
        rec = {"q": q, "collection": coll, "score": round(hits[0]["score"], 4) if hits else None,
               "section": top.get("section"), "doc": top.get("title"),
               "source_url": top.get("source_url"), "excerpt": (top.get("text") or "")[:260].replace("\n", " ")}
        rows.append(rec)
        print(json.dumps(rec, ensure_ascii=False))
    return rows


def upsert(coll: str, batch: list[dict], dim: int) -> int:
    pts, meta = [], []
    for rec in batch:
        meta.append(rec)
    vecs = embed([m["text"] for m in meta])
    for m, v in zip(meta, vecs, strict=True):
        key = f"{coll}:{m.get('path') or m.get('page_path')}:{m['section']}:{hashlib.sha256(m['text'].encode()).hexdigest()[:12]}"
        pts.append({"id": hashlib.md5(key.encode()).hexdigest(), "vector": v,
                    "payload": {**m, "publishable": m["licence"] in {"CC0-1.0", "Apache-2.0", "MIT", "CC-BY-4.0"}}})
    req("PUT", f"{QDRANT}/collections/{coll}/points?wait=true", {"points": pts})
    return len(pts)


def drain(coll: str, records, dim: int, budget: float, state: dict) -> int:
    start, total, batch = time.time(), 0, []
    for rec in records:
        batch.append(rec)
        if len(batch) < 8:
            continue
        if time.time() - start > budget:
            state[coll] = {"points": total, "stopped": "budget", "pending_batch": len(batch)}
            return total
        total += upsert(coll, batch, dim)
        batch = []
    if batch:
        total += upsert(coll, batch, dim)
    state[coll] = {"points": total, "stopped": "complete"}
    return total


def main() -> None:
    global EMBED
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/standards/repos")
    ap.add_argument("--budget", type=float, default=600.0)
    ap.add_argument("--cap-eips", type=int, default=1200)
    ap.add_argument("--embed", default=EMBED)
    ap.add_argument("--sol-licence", default="")
    args = ap.parse_args()
    EMBED = args.embed
    root = Path(args.root)
    dim = len(embed(["dimension probe"])[0])
    print("embed_dim", dim, "endpoint", EMBED)
    before = proof_block("BEFORE")
    sol_licence = args.sol_licence or detect_licence(root / "developer-content")
    print("solana_licence_detected", sol_licence)
    ensure(EIPS_COLL, dim)
    ensure(SOL_COLL, dim)
    state: dict = {"embed_dim": dim, "eips_commit": EIPS_SHA, "solana_licence": sol_licence}
    eips = list(eip_records(root / "EIPs", "CC0-1.0", EIPS_SHA, args.cap_eips))
    print("eip_chunks_total", len(eips), "eip_docs", len({c["path"] for c in eips}))
    first = drain(EIPS_COLL, iter(eips), dim, args.budget / 2, state)
    sol = list(sol_records(root / "developer-content", sol_licence, "97a223daedfb5c3b696b709e1a9246a861f2141b"))
    print("sol_chunks_total", len(sol), "sol_docs", len({c["page_path"] for c in sol}))
    second = drain(SOL_COLL, iter(sol), dim, args.budget / 2, state)
    print("points_written", {EIPS_COLL: first, SOL_COLL: second})
    after = proof_block("AFTER")
    out = Path("data/standards")
    out.mkdir(parents=True, exist_ok=True)
    (out / "state.json").write_text(json.dumps({**state, "before": before, "after": after,
                                                "eip_chunks": len(eips), "sol_chunks": len(sol),
                                                "eip_docs": len({c["path"] for c in eips}),
                                                "sol_docs": len({c["page_path"] for c in sol})}, indent=2))


if __name__ == "__main__":
    main()
