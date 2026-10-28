#!/usr/bin/env python3
"""Build the materialised "juicy content" ranking from the shared Qdrant store.

RugBrain and ContextCrypto already read the SAME Qdrant (``rmi-qdrant-v2``); this
script copies nothing. It scans the incident/advisory collections plus the
detection-doc libraries that live inside ``codebase_reference_v2_8b``, scores
each record with :mod:`rugbrain.intel.juicy_rank`, and writes ONE small JSON
file. The API then serves that file, so a request never recomputes a ranking.

Run nightly (see ``deploy/systemd/rugbrain-juicy-ranking.{service,timer}``)::

    python3 scripts/rank_juicy_content.py [--out PATH] [--limit N]

Scoring weights are a private seam: ``RUGBRAIN_JUICY_WEIGHTS`` points at a JSON
file owned outside this repo; without it the builder scores on the documented
equal-weight baseline and stamps ``weights_source``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data" / "ranking" / "juicy_ranking.json"
RANK_MODULE = REPO_ROOT / "src" / "rugbrain" / "intel" / "juicy_rank.py"
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
MAX_POINTS = int(os.getenv("JUICY_MAX_PER_COLLECTION", "1500"))
PER_KIND_LIMIT = int(os.getenv("JUICY_PER_KIND_LIMIT", "40"))

#: (collection, kind) — all public in ``knowledge_boundary_tables``; nothing internal.
SOURCES: tuple[tuple[str, str], ...] = (
    ("exploits_8b", "exploit"),
    ("defi_exploits_8b", "exploit"),
    ("hacks_v2_8b", "hack"),
    ("scam_intel_v2_8b", "scam"),
    ("scam_patterns_8b", "scam"),
    ("known_scams_8b", "scam"),
    ("crypto_vulnerabilities_8b", "advisory"),
    ("security_alerts_8b", "advisory"),
)

#: Detection docs live inside the shared corpus, addressed by ``library_id``.
DETECTION_COLLECTION = "codebase_reference_v2_8b"
DETECTION_LIBRARIES: tuple[str, ...] = (
    "docs:detection-playbook",
    "docs:advisory-databases",
    "docs:tailscale-advisories",
    "docs:zero-day-advisories",
)

TITLE_KEYS = ("title", "name", "protocol", "advisory_id", "source_key")
SUMMARY_KEYS = ("description", "text", "content", "summary")
DATE_KEYS = ("date_iso", "date", "published_at", "fetched_at", "ingested_at", "created_at")
SOURCE_KEYS = ("source", "source_name", "library_name")


def _rank_module() -> Any:
    """Import the pure scoring module by path (no package init, no heavy deps)."""
    spec = importlib.util.spec_from_file_location("juicy_rank", RANK_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def scroll(collection: str, flt: dict[str, Any] | None = None, cap: int = MAX_POINTS) -> list[dict]:
    """Page through a collection; returns points (never deletes, never writes)."""
    points: list[dict] = []
    offset: Any = None
    while len(points) < cap:
        body: dict[str, Any] = {"limit": 256, "with_payload": True, "with_vector": False}
        body.update({k: v for k, v in (("filter", flt), ("offset", offset)) if v})
        request = urllib.request.Request(  # noqa: S310 — loopback Qdrant
            f"{QDRANT_URL}/collections/{collection}/points/scroll",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:  # noqa: S310
                result = json.load(response)["result"]
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"[warn] {collection}: {exc}", file=sys.stderr)
            break
        points.extend(result.get("points") or [])
        offset = result.get("next_page_offset")
        if not offset:
            break
    return points[:cap]


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((payload[key] for key in keys if payload.get(key)), None)


def normalise(point: dict[str, Any], collection: str, kind: str, rank: Any) -> dict[str, Any]:
    """Map a Qdrant payload into the one shape the ranking serves."""
    payload = point.get("payload") or {}
    provenance = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    timestamp = rank.parse_timestamp(_first(payload, DATE_KEYS))
    summary = str(_first(payload, SUMMARY_KEYS) or "")
    headline = summary.split("\n", 1)[0][:90].strip()
    title = str(_first(payload, TITLE_KEYS) or payload.get("entity") or headline or "untitled")
    return {
        "id": str(point.get("id")),
        "collection": collection,
        "kind": kind,
        "title": rank.redact(title)[:220],
        "summary": rank.redact(summary)[:400],
        "timestamp": timestamp,
        "date": (
            datetime.fromtimestamp(timestamp, UTC).isoformat() if timestamp is not None else None
        ),
        "severity": str(payload.get("severity") or "unknown").lower(),
        "library_id": payload.get("library_id"),
        "library_name": payload.get("library_name"),
        "source": str(_first(payload, SOURCE_KEYS) or provenance.get("source") or ""),
        "source_url": str(payload.get("source_url") or provenance.get("source_url") or ""),
        "impact_usd": payload.get("loss_usd") or payload.get("amount"),
        "tags": [str(tag) for tag in (payload.get("tags") or [])][:12],
    }


def collect(rank: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for collection, kind in SOURCES:
        points = scroll(collection)
        counts[collection] = len(points)
        items.extend(normalise(point, collection, kind, rank) for point in points)

    flt = {"must": [{"key": "library_id", "match": {"any": list(DETECTION_LIBRARIES)}}]}
    detection = scroll(DETECTION_COLLECTION, flt=flt)
    counts[f"{DETECTION_COLLECTION}:detection-docs"] = len(detection)
    items.extend(normalise(point, DETECTION_COLLECTION, "detection", rank) for point in detection)
    return items, counts


def build(out: Path, limit: int) -> dict[str, Any]:
    rank = _rank_module()
    now_ts = time.time()
    weights, weights_source = rank.load_weights()
    items, counts = collect(rank)
    ranked = rank.rank_items(items, now_ts, limit=limit, weights=weights)
    # A flat top-N starves whole families: keep the global top-N AND every kind's top.
    merged = {(item["collection"], item["id"]): item for item in ranked}
    for kind in {item["kind"] for item in items}:
        subset = [item for item in items if item["kind"] == kind]
        for item in rank.rank_items(subset, now_ts, limit=PER_KIND_LIMIT, weights=weights):
            merged.setdefault((item["collection"], item["id"]), item)
    ranked = sorted(merged.values(), key=lambda entry: entry["score"], reverse=True)
    by_kind = dict(Counter(item["kind"] for item in ranked))
    document = {
        "schema_version": rank.SCHEMA_VERSION,
        "generated_at": datetime.fromtimestamp(now_ts, UTC).isoformat(),
        "generated_at_epoch": int(now_ts),
        "half_life_days": rank.HALF_LIFE_DAYS,
        "weights": weights,
        "weights_source": weights_source,
        "sources": [name for name, _ in SOURCES]
        + [f"{DETECTION_COLLECTION} ({'|'.join(DETECTION_LIBRARIES)})"],
        "counts": {"scanned": len(items), "kept": len(ranked), "by_kind": by_kind, **counts},
        "items": ranked,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(document, indent=1, sort_keys=False), encoding="utf-8")
    os.replace(tmp, out)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the materialised juicy-content ranking")
    default_out = Path(os.getenv("JUICY_RANKING_FILE", DEFAULT_OUT))
    parser.add_argument("--out", type=Path, default=default_out)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    document = build(args.out, args.limit)
    counts = document["counts"]
    print(
        f"juicy ranking -> {args.out} scanned={counts['scanned']} kept={counts['kept']} kinds={counts['by_kind']}"
    )
    if not document["items"]:
        print("[error] no items collected — refusing to serve an empty ranking", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
