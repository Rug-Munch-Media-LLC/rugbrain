"""DeFi protocol MECHANICS docs -> heading chunks -> 4096-d points, with provenance.

Run: python3 scripts/ingest_defi_protocol_docs.py [protocol ...]

Answers "how does X actually work" from protocol text instead of 4,000 lines of
Solidity. Every payload carries {protocol, repo, ref, path, section, licence,
publishable}; a missing licence is publishable=false and is never guessed.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from defi_docs_chunk import chunk  # noqa: E402
from defi_docs_config import REPOS, is_candidate, pick_pages  # noqa: E402
from defi_docs_fetch import clone, git, licence_at_ref  # noqa: E402

QDRANT_URL = os.environ.get("QDRANT_URL", "http://127.0.0.1:6334")
COLL = "protocol_design_8b"
DIM_REQUIRED = 4096
SRC = Path(os.environ.get("DEFI_SRC", "/srv/work/wt/rb-defi48/data/defi_protocol_docs/src"))


LANE_ENV = Path("/srv/models/embed-lane.env")


def env_keys() -> list[str]:
    """Bearer keys for the embed lane: *_KEY / *API_KEY from env or the lane env file.

    The 8B lane guards :8024 with --api-key ${LLAMA_EMBED_KEY}; a name filter of
    "API_KEY" alone matched nothing and every probe 401'd.
    """
    candidates = [os.environ[name] for name in ("LLAMA_EMBED_KEY", "EMBED_API_KEY", "API_KEY")
                  if os.environ.get(name)]
    candidates += [value for name, value in os.environ.items()
                   if ("API_KEY" in name.upper() or name.upper().endswith("_KEY")) and value]
    if LANE_ENV.exists():
        for line in LANE_ENV.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                value = line.split("=", 1)[1].strip().strip('"\'')
                if len(value) >= 8:
                    candidates.append(value)
    seen: set[str] = set()
    return [key for key in candidates if not (key in seen or seen.add(key))]


MAX_EMBED_CHARS = 6000  # llama-server closes the connection on an over-long prompt


ACTIVE_MODEL = "local"  # set by probe_lane(); the hosted 8B lane needs its real model id


def embed(client: httpx.Client, base: str, key: str, texts: list[str],
          model: str = "", batch_size: int = 4) -> list[list[float]]:
    model = model or ACTIVE_MODEL
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        chunk = [text[:MAX_EMBED_CHARS] for text in texts[start:start + batch_size]]
        for attempt in range(3):
            try:
                response = client.post(f"{base}/v1/embeddings", headers=headers, timeout=900,
                                       json={"model": model, "input": chunk})
                response.raise_for_status()
                vectors += [item["embedding"] for item in response.json()["data"]]
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5)
    return vectors


def point_id(protocol: str, path: str, section: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"defi:{protocol}:{path}:{section}"))


def ingest_one(protocol: str, cfg: dict, client: httpx.Client, base: str, key: str,
               cap: int) -> dict:
    repo, attempts = clone(protocol, cfg["candidates"], SRC)
    record = {"protocol": protocol, "repo": cfg["candidates"][0], "site": cfg["site"],
              "clone_attempts": attempts}
    if repo is None:
        return {**record, "status": "skipped", "reason": "no docs repo clonable"}
    ref = git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    spdx, publishable, note, licence_file = licence_at_ref(repo, ref)
    files = sorted(str(p.relative_to(repo)) for p in repo.rglob("*")
                   if p.is_file() and p.suffix.lower() in (".md", ".mdx"))
    pages = pick_pages(files)
    meta = {"protocol": protocol, "repo": cfg["candidates"][0], "ref": ref,
            "licence": spdx, "publishable": publishable, "licence_file": licence_file,
            "licence_note": note, "internal_only": not publishable, "site": cfg["site"],
            "source": "defi_protocol_docs", "doc_type": "protocol_mechanics",
            "retrieved_on": time.strftime("%Y-%m-%d")}
    docs: list[dict] = []
    for rel in pages:
        if len(docs) >= cap:
            break
        try:
            docs += chunk(protocol, rel, (repo / rel).read_text(errors="ignore"), meta)
        except Exception as exc:  # one unreadable page must not kill the protocol
            print(f"chunk_fail {rel}: {exc}", file=sys.stderr)
    docs = docs[:cap]
    if not docs:
        return {**record, "status": "skipped", "reason": "no mechanics text",
                "ref": ref, "licence": spdx, "pages_total": len(files)}
    started = time.time()
    vectors = embed(client, base, key, [doc["text"] for doc in docs])
    if len(vectors[0]) != DIM_REQUIRED:
        raise RuntimeError(f"lane {base} returned {len(vectors[0])}-d, need {DIM_REQUIRED}")
    points = [{"id": point_id(protocol, doc["path"], doc["section"]), "vector": vector,
               "payload": doc} for doc, vector in zip(docs, vectors)]
    for start in range(0, len(points), 64):
        client.put(f"{QDRANT_URL}/collections/{COLL}/points?wait=true",
                   json={"points": points[start:start + 64]}, timeout=900).raise_for_status()
    return {**record, "status": "ingested", "ref": ref, "licence": spdx,
            "licence_file": licence_file, "licence_note": note, "publishable": publishable,
            "pages_total": len(files), "pages_selected": len(pages),
            "pages_excluded": len([f for f in files if not is_candidate(f)]),
            "points": len(points), "secs": round(time.time() - started, 1),
            "with_formula": sum(1 for doc in docs if doc["has_formula"]),
            "with_table": sum(1 for d in docs if d["has_table"])}


def main() -> int:
    protocols = sys.argv[1:] or list(REPOS)
    with httpx.Client() as client:
        base, key, model, dim = probe_lane(client)
        if not base:
            raise SystemExit(
                "ABORT: no corpus-valid 4096-d embed lane answered "
                "(siliconflow.com Qwen3-Embedding-8B, local :8024) - never a 1024-d fallback")
        cap = int(os.environ.get("DEFI_CAP", "350"))
        results = [ingest_one(name, REPOS[name], client, base, key, cap) for name in protocols]
        print(json.dumps({"lane": base, "lane_model": model, "dim": dim, "cap": cap,
                          "collection": COLL, "results": results}, indent=2))
    return 0


SILICONFLOW_BASE = "https://api.siliconflow.com"
SILICONFLOW_MODEL = "Qwen/Qwen3-Embedding-8B"
SILICONFLOW_KEY_FILE = Path(os.environ.get("SILICONFLOW_KEY_FILE",
                                           "/srv/secrets/siliconflow_api_key"))


def siliconflow_key() -> str:
    """File-first: a stale SILICONFLOW_API_KEY in the env must never win over the file."""
    if SILICONFLOW_KEY_FILE.exists():
        value = SILICONFLOW_KEY_FILE.read_text().strip()
        if value:
            return value
    return os.environ.get("SILICONFLOW_API_KEY", "")


def probe_lane(client: httpx.Client):
    """siliconflow .com 4096-d -> local :8024 4096-d -> abort. Never a 1024-d lane."""
    global ACTIVE_MODEL
    lanes = [("siliconflow.com", SILICONFLOW_BASE, SILICONFLOW_MODEL, siliconflow_key())]
    for key in (env_keys() or [""]):
        lanes.append(("local :8024", "http://127.0.0.1:8024", "local", key))
    for name, base, model, key in lanes:
        try:
            vector = embed(client, base, key, ["dimension probe"], model=model)[0]
        except Exception as exc:
            print(f"lane {name} unusable: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)
            continue
        if len(vector) != DIM_REQUIRED:
            print(f"lane {name} answered {len(vector)}-d != {DIM_REQUIRED}: not corpus-valid",
                  file=sys.stderr)
            continue
        ACTIVE_MODEL = model
        return base, key, model, len(vector)
    return None, "", "", 0


if __name__ == "__main__":
    raise SystemExit(main())
