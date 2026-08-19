"""Create the Qwen3 (2560-dim) collection schema on rmi-qdrant-v2 (:6334).

Mirrors the legacy rmi-qdrant (:6333) collection list — same names, same
payload settings — but with dense vectors at 2560 dims (Qwen3-Embedding-4B)
instead of 1024 (bge-m3). Sparse bm25 vectors and HNSW settings are carried
over from the legacy config where present.

Usage:
    python3 scripts/create_qdrant_v2_schema.py [--collections c1,c2] [--dry-run]

Default: mirror every non-empty legacy collection. Empty/pointless legacy
collections are skipped unless --all is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from urllib.error import URLError

LEGACY = "http://127.0.0.1:6333"
V2 = "http://127.0.0.1:6334"
DIM = 2560


def _get(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _put(url: str, payload: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collections", default="", help="comma-separated subset")
    ap.add_argument("--all", action="store_true", help="include empty legacy collections")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cols = [c["name"] for c in _get(f"{LEGACY}/collections")["result"]["collections"]]
    if args.collections:
        subset = {c.strip() for c in args.collections.split(",") if c.strip()}
        cols = [c for c in cols if c in subset]

    created, skipped_empty, errors = [], [], []
    for name in cols:
        try:
            info = _get(f"{LEGACY}/collections/{name}")["result"]
        except URLError as e:
            errors.append(f"{name}: legacy read failed: {e}")
            continue

        pts = info.get("points_count", 0)
        if pts == 0 and not args.all:
            skipped_empty.append(name)
            continue

        cfg = info.get("config", {})
        params = cfg.get("params", {})
        vectors = params.get("vectors", {})
        # Normalize to named-vector schema at 2560 dims.
        if isinstance(vectors, dict) and "dense" in vectors:
            dense_cfg = dict(vectors["dense"])
            dense_cfg["size"] = DIM
            new_vectors: dict = {"dense": dense_cfg}
            if isinstance(vectors, dict) and "bm25" in vectors:
                new_vectors["bm25"] = {"multiplier": 0.9, "index": {"type": "bm25"}}
        else:
            new_vectors = {"dense": {"size": DIM, "distance": "Cosine"}}

        payload = {
            "vectors": new_vectors,
            "on_disk_payload": params.get("on_disk_payload", True),
            "hnsw_config": cfg.get("hnsw_config") or {"m": 16, "ef_construct": 100},
        }

        if args.dry_run:
            print(f"[dry-run] would create {name}: dense={DIM}d points={pts}")
            continue
        try:
            _put(f"{V2}/collections/{name}", payload)
            created.append(f"{name} ({pts} pts)")
        except URLError as e:
            errors.append(f"{name}: create failed: {e}")

    print(f"\nCreated {len(created)}: {', '.join(created)}" if created else "\nNothing created")
    if skipped_empty:
        print(f"Skipped empty: {len(skipped_empty)} ({', '.join(skipped_empty[:8])}…)")
    if errors:
        print(f"ERRORS: {errors}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
