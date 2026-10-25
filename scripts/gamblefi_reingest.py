"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

Clean and re-ingest the ``gamblefi_docs`` collection with a prose extractor.

    --clean     judge every stored point, delete the ones that are not prose,
                and record each rejection (id, reason) in
                ``data/gamblefi_rejects.jsonl`` — never silently dropped.
    --reingest  fetch the sources below, extract markdown, judge, embed and
                upsert with a deterministic uuid5 (re-running is idempotent).
    --dry-run   judge and report only; no DELETE, no upsert.

Licence discipline: ``publisher-terms`` sources are stored with
``publishable=false`` and ``licence`` kept verbatim in the payload.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rugbrain.page_text import extract_page_text  # noqa: E402
from rugbrain.text_quality import judge_text  # noqa: E402

QDRANT = "http://127.0.0.1:6334"
COLLECTION = "gamblefi_docs"
SF_KEY_FILE = os.getenv("SILICONFLOW_KEY_FILE", "/srv/secrets/siliconflow_api_key")
EMBED_URL = os.getenv("EMBED_URL", "https://api.siliconflow.com/v1/embeddings")
EMBED_MODEL = os.getenv("SILICONFLOW_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")
LOCAL_EMBED_FILES = ("/srv/models/embed-lane.env", "/srv/secrets/embed-lane.env")


def _embed_key(url: str) -> str:
    """File-first key for the lane that will actually be called. Never logged.

    2026-10-25: this script posted to local :8024 with no Authorization header
    and died on HTTP 401 Unauthorized -- the key existed the whole time in
    /srv/models/embed-lane.env. Local and .com keys are DIFFERENT; .cn is never
    tried without SILICONFLOW_CN_API_KEY.
    """
    if "siliconflow.cn" in url and not os.getenv("SILICONFLOW_CN_API_KEY", "").strip():
        raise SystemExit(f"refusing .cn lane without SILICONFLOW_CN_API_KEY: {url}")
    if "siliconflow" in url:
        key = os.getenv("SILICONFLOW_API_KEY", "").strip()
        if key:
            return key
        try:
            return open(SF_KEY_FILE, encoding="utf-8").read().strip()
        except OSError:
            return ""
    for path in LOCAL_EMBED_FILES:
        try:
            for line in open(path, encoding="utf-8"):
                k, _, v = line.partition("=")
                if k.strip() in ("LLAMA_EMBED_KEY", "SHIM_KEY") and v.strip():
                    return v.strip().strip('"')
        except OSError:
            continue
    for name in ("LLAMA_EMBED_KEY", "QWEN_EMBED_API_KEY", "LOCAL_EMBED_KEY"):
        if os.getenv(name, "").strip():
            return os.getenv(name).strip()
    return ""
DIM = 4096
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

#: (publisher, url, licence, publishable) — prose sources only.
SOURCES = (
    ("Azuro", "https://docs.azuro.org/", "publisher-terms", False),
    ("Beam", "https://docs.onbeam.com/", "publisher-terms", False),
    ("Immutable", "https://docs.immutable.com/", "publisher-terms", False),
    ("Immutable contracts", "https://raw.githubusercontent.com/immutable/contracts/main/README.md", "MIT", True),
    ("Roobet", "https://roobet.com/fair", "publisher-terms", False),
    ("Pixels", "https://docs.pixels.xyz/", "publisher-terms", False),
)


def _http(url: str, payload: dict | None = None, timeout: int = 25) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": UA,
                 **({"Authorization": f"Bearer {_embed_key(url)}"} if url.endswith("/embeddings") else {})},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def scroll_all() -> list[dict]:
    out: list[dict] = []
    offset = None
    while True:
        body = {"limit": 100, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        result = _http(f"{QDRANT}/collections/{COLLECTION}/points/scroll", body)["result"]
        out.extend(result["points"])
        offset = result.get("next_page_offset")
        if not offset:
            return out


def content_of(point: dict) -> str:
    payload = point.get("payload") or {}
    return str(payload.get("content") or payload.get("text") or "")


def clean(dry_run: bool) -> int:
    points = scroll_all()
    rejects = []
    for point in points:
        verdict = judge_text(content_of(point))
        if not verdict.ok:
            rejects.append({"id": point["id"], "reason": verdict.reason, **verdict.as_dict()})
    kept = len(points) - len(rejects)
    print(f"before={len(points)} noise={len(rejects)} prose={kept}")
    for row in rejects[:40]:
        print(f"  DEL {row['id']} {row['reason']} words={row['words']}")
    log = ROOT / "data" / "gamblefi_rejects.jsonl"
    if not dry_run:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in rejects)
        if rejects:
            _http(f"{QDRANT}/collections/{COLLECTION}/points/delete?wait=true",
                  {"points": [r["id"] for r in rejects]})
        after = _http(f"{QDRANT}/collections/{COLLECTION}")["result"]["points_count"]
        print(f"after={after} rejects_logged={len(rejects)} -> {log.name}")
    return 0 if len(rejects) else 1


def _embed(text: str) -> list[float]:
    result = _http(EMBED_URL, {"input": [text[:6000]], "model": EMBED_MODEL})
    vector = result["data"][0]["embedding"]
    if len(vector) != DIM:
        raise SystemExit(f"REFUSED: embedding dim {len(vector)} != {DIM}")
    return vector


def reingest() -> int:
    written = refused = 0
    for publisher, url, licence, publishable in SOURCES:
        try:
            markup = urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30
            ).read().decode("utf-8", "ignore")
        except (urllib.error.URLError, OSError) as exc:
            print(f"REFUSE {publisher}: {type(exc).__name__}")
            refused += 1
            continue
        text = extract_page_text(markup)
        verdict = judge_text(text)
        if not verdict.ok:
            print(f"REFUSE {publisher}: {verdict.reason} words={verdict.words} "
                  f"script_ratio={verdict.script_ratio:.2f}")
            refused += 1
            continue
        sha = hashlib.sha256(text.encode()).hexdigest()
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{url}#{sha}"))
        payload = {
            "content": text[:20000],
            "source": url,
            "collection": COLLECTION,
            "chain": "multi",
            "severity": "info",
            "lane_collection": COLLECTION,
            "metadata": {
                "url": url, "title": f"{publisher} — docs", "publisher": publisher,
                "licence": licence, "publishable": publishable,
                "source_kind": "official", "section": "docs",
                "fetched_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "page_sha256": sha, "extractor": "page_text/v1",
                "quality": verdict.as_dict(),
            },
        }
        _http(f"{QDRANT}/collections/{COLLECTION}/points/upsert?wait=true",
              {"points": [{"id": point_id, "vector": _embed(text), "payload": payload}]})
        print(f"WROTE {publisher} id={point_id} words={verdict.words} publishable={publishable}")
        written += 1
    total = _http(f"{QDRANT}/collections/{COLLECTION}")["result"]["points_count"]
    print(f"reingest written={written} refused={refused} collection_total={total}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--reingest", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rc = 0
    if args.clean:
        rc |= clean(args.dry_run)
    if args.reingest:
        rc |= reingest()
    sys.exit(rc)
