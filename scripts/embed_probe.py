#!/usr/bin/env python3
"""Canonical embed-lane probe. Prints which lane works, its dims and latency.

Order (file-first, stale env never wins):
  1. SiliconFlow 4096-d  key file /srv/secrets/siliconflow_api_key
  2. local llama.cpp 8B  key LLAMA_EMBED_KEY in /srv/models/embed-lane.env  (slow: ~2 min/doc cold)
  3. local 0.6B 1024-d   (:8004) -- NOT corpus-valid, reported for completeness only
"""
from __future__ import annotations
import json, os, time, urllib.request

SILICONFLOW_KEY = "/srv/secrets/siliconflow_api_key"
LOCAL_ENV = "/srv/models/embed-lane.env"


def _key(path: str, name: str | None = None) -> str:
    try:
        raw = open(path).read().strip()
    except OSError:
        return ""
    if name is None:
        return raw.splitlines()[0].strip()
    for line in raw.splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    return ""


def probe(url: str, key: str, model: str, timeout: int = 180) -> dict:
    body = json.dumps({"input": ["probe"], "model": model}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "authorization": f"Bearer {key}", "content-type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        dim = len(data["data"][0]["embedding"])
        return {"ok": True, "dim": dim, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001 - probe reports, never raises
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:80]}",
                "ms": int((time.time() - t0) * 1000)}


def main() -> int:
    lanes = [
        ("siliconflow-8b", "https://api.siliconflow.com/v1/embeddings",
         _key(SILICONFLOW_KEY), "Qwen/Qwen3-Embedding-8B", 4096),
        ("local-8b", "http://127.0.0.1:8024/v1/embeddings",
         _key(LOCAL_ENV, "LLAMA_EMBED_KEY"), "qwen3-embedding-8b", 4096),
        ("local-0.6b", "http://127.0.0.1:8004/v1/embeddings",
         _key(LOCAL_ENV, "LLAMA_EMBED_KEY"), "qwen", 1024),
    ]
    working = []
    for name, url, key, model, want in lanes:
        r = probe(url, key, model)
        r.update({"lane": name, "want_dim": want, "corpus_valid": r.get("dim") == 4096})
        working.append(r)
        print(json.dumps(r))
    ok = [w for w in working if w["ok"] and w["corpus_valid"]]
    print(json.dumps({"usable_corpus_lane": ok[0]["lane"] if ok else None}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
