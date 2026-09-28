"""Create Qdrant payload indexes for the mission collections, from evidence.

Why this exists: with an empty ``payload_schema`` every filtered query is a full
payload scan. Measured on this host, a ``news_v2`` category filter cost 22.8x
(25.7ms -> 584.5ms), ``scam_intel_v2`` chain 29.7x, ``threat_intel_v2`` kind
18.8x. An index is the cheapest available win.

Unlike the static ``qdrant_date_plan.INDEX_PLAN`` this derives the field list
from the payload that actually exists: each candidate field is counted over a
sample of points and only indexed when it is present often enough to be worth
building. An index on an absent field sits empty forever and reads as coverage
that does not exist.

Safe to re-run: Qdrant treats creating an existing index as a no-op.
Default is a dry run; pass ``--apply`` to write.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any

QDRANT = "http://127.0.0.1:6334"

#: field path -> the index type Qdrant should build for it.
DATETIME = {"type": "datetime"}
KEYWORD = {"type": "keyword"}

#: Ordered by trust: a dedicated event date beats a generic published date.
CANDIDATES: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "defi_exploits": [
        ("date", DATETIME), ("date_ts", DATETIME),
        ("chain", KEYWORD), ("severity", KEYWORD), ("protocol", KEYWORD),
        ("attack_vector", KEYWORD),
    ],
    "news_v2": [
        ("metadata.published", DATETIME), ("metadata.ts", DATETIME),
        ("chain", KEYWORD), ("severity", KEYWORD), ("source", KEYWORD),
        ("metadata.kind", KEYWORD),
    ],
    "threat_intel_v2": [
        ("captured_at", DATETIME), ("metadata.published", DATETIME),
        ("chain", KEYWORD), ("severity", KEYWORD), ("kind", KEYWORD),
    ],
    "scam_intel_v2": [
        ("metadata.reported_date", DATETIME), ("metadata.date", DATETIME),
        ("chain", KEYWORD), ("severity", KEYWORD), ("source", KEYWORD),
    ],
    "scan_intel": [
        ("scanned_at", DATETIME), ("chain", KEYWORD),
        ("risk_level", KEYWORD), ("kind", KEYWORD),
    ],
    "known_scams": [("chain", KEYWORD), ("severity", KEYWORD), ("source", KEYWORD)],
    "scam_patterns": [
        ("chain", KEYWORD), ("severity", KEYWORD),
        ("attack_vector", KEYWORD), ("protocol", KEYWORD),
    ],
    "wallet_profiles": [("chain", KEYWORD), ("metadata.label", KEYWORD)],
    "wallet_v2": [
        ("metadata.scanned_at", DATETIME), ("chain", KEYWORD), ("severity", KEYWORD),
    ],
    "exploits": [("chain", KEYWORD), ("severity", KEYWORD), ("date", DATETIME)],
    "crypto_vulnerabilities": [("chain", KEYWORD), ("severity", KEYWORD)],
    "contract_audits": [("severity", KEYWORD), ("chain", KEYWORD)],
    "regulation_enforcement": [("severity", KEYWORD), ("chain", KEYWORD)],
    "education": [("chain", KEYWORD), ("severity", KEYWORD)],
    "live_threats": [
        ("chain", KEYWORD), ("severity", KEYWORD), ("status", KEYWORD),
    ],
}

#: A field is worth indexing once it is present on this share of the sample.
MIN_PRESENCE = 0.05
SAMPLE = 200


def request(method: str, path: str, body: Any = None, timeout: int = 180) -> Any:
    """Call the Qdrant REST API. Raises RuntimeError with the Qdrant error text."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 — fixed loopback host, not user input
        QDRANT + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            loaded = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode(errors="replace")
        raise RuntimeError(f"qdrant {method} {path} -> {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"qdrant unreachable at {QDRANT}: {exc.reason}") from exc
    return loaded.get("result")


def sample_points(collection: str, limit: int = SAMPLE) -> list[dict[str, Any]]:
    """Scroll up to ``limit`` points with payloads, no vectors."""
    body = {"limit": limit, "with_payload": True, "with_vector": False}
    return request("POST", f"/collections/{collection}/points/scroll", body)["points"]


def dig(payload: dict[str, Any], path: str) -> Any:
    """Read a dotted path out of a payload; None when any hop is missing."""
    node: Any = payload
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def plan_for(collection: str) -> dict[str, Any]:
    """Return {'index': [...], 'skip': [...]} for one collection, from evidence."""
    points = sample_points(collection)
    existing = (request("GET", f"/collections/{collection}") or {}).get("payload_schema") or {}
    index: list[dict[str, Any]] = []
    skip: list[dict[str, Any]] = []
    if not points:
        return {"index": index, "skip": [{"field": "*", "reason": "collection is empty"}]}
    for field, spec in CANDIDATES.get(collection, []):
        if field in existing:
            skip.append({"field": field, "reason": "already indexed"})
            continue
        hits = sum(1 for p in points if dig(p.get("payload") or {}, field) not in (None, "", [], {}))
        share = hits / len(points)
        if share >= MIN_PRESENCE:
            index.append({"field": field, "params": spec, "presence": round(share, 3)})
        else:
            skip.append({"field": field, "reason": f"present on only {share:.0%} of sample"})
    return {"index": index, "skip": skip}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="create the indexes (default: dry run)")
    ap.add_argument("--collection", help="limit to one collection")
    args = ap.parse_args()

    targets = [args.collection] if args.collection else list(CANDIDATES)
    created = skipped = failed = 0
    for collection in targets:
        try:
            result = plan_for(collection)
        except RuntimeError as exc:
            print(f"{collection}: ERROR {exc}")
            failed += 1
            continue
        if not result["index"]:
            print(f"{collection}: nothing to index")
        for entry in result["index"]:
            field = entry["field"]
            if not args.apply:
                print(f"  would index {collection}.{field} ({entry['presence']:.0%} present)")
                continue
            try:
                request(
                    "PUT",
                    f"/collections/{collection}/index",
                    {"field_name": field, "field_schema": entry["params"]},
                )
                print(f"  indexed {collection}.{field} ({entry['presence']:.0%} present)")
                created += 1
            except RuntimeError as exc:
                print(f"  FAILED {collection}.{field}: {exc}")
                failed += 1
        for entry in result["skip"]:
            if entry["field"] != "*":
                print(f"  skipped {collection}.{entry['field']}: {entry['reason']}")
                skipped += 1
    verb = "created" if args.apply else "would create"
    print(f"\n{verb} {created if args.apply else 'N/A'} indexes; skipped {skipped}; failed {failed}")


if __name__ == "__main__":
    main()
