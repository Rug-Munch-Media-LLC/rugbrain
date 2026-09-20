"""Auto-generate docs/COLLECTION_REGISTRY.md from live Qdrant.

The registry drifted a month behind reality (news_v2 listed 4,124 vs 143,985
live) and caused wrong conclusions about duplication. This script makes drift
impossible: the doc is generated, never hand-edited.

Usage:
    .venv/bin/python scripts/registry_report.py            # print table
    .venv/bin/python scripts/registry_report.py --write    # update the doc

Freshness is sampled (300 points/collection) — treat it as "recent activity",
not an exact max. A weekly systemd timer (rugbrain-registry.timer) runs --write.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

QDRANT = os.getenv("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
DOC = Path(__file__).resolve().parent.parent / "docs" / "COLLECTION_REGISTRY.md"
SAMPLE = 300
STALE_DAYS = 90
TS_FIELDS = ("updated_at", "created_at", "timestamp", "ingested_at", "date", "ts", "published")

# Hand-maintained knowledge the generator cannot infer from Qdrant.
OWNERSHIP_NOTES = """## Ownership notes

- `rgm_*` collections (wallet graph) — owned by **rmi-backend** (Neo4j entity
  resolution), not RugBrain. Freshness is rmi-backend's responsibility.
- `internal/*` libraries live in the **ContextCrypto** corpus
  (`codebase_reference_v2`), not here — code belongs to the MCP, knowledge to
  RugBrain (see `DATA-PLACEMENT.md` and `RAG-MCP-BOUNDARY.md`).
- Frozen sources are flagged automatically when the sampled newest point is
  older than 90 days."""


def _get(path: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(f"{QDRANT}{path}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _post(path: str, body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        f"{QDRANT}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def collections() -> list[str]:
    data = _get("/collections")
    return sorted(c["name"] for c in data["result"]["collections"] if not c["name"].startswith("."))


def count(name: str) -> int:
    return int(_post(f"/collections/{name}/points/count", {"exact": True})["result"]["count"])


def sample(name: str, n: int = SAMPLE) -> list[dict]:
    res = _post(
        f"/collections/{name}/points/scroll",
        {"limit": n, "with_payload": True, "with_vector": False},
    )
    return [p.get("payload") or {} for p in res["result"]["points"]]


def _consider(value: object, best: int | None) -> int | None:
    if isinstance(value, (int, float)) and value > 1_000_000_000:
        return int(value) if best is None else max(best, int(value))
    if isinstance(value, str) and len(value) >= 10:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(dt.timestamp()) if best is None else max(best, int(dt.timestamp()))
        except ValueError:
            return best
    return best


def newest_ts(payloads: list[dict]) -> datetime | None:
    """Newest timestamp in the sample — top-level or nested in ``metadata``."""
    best: int | None = None
    for pl in payloads:
        for f in TS_FIELDS:
            if f in pl:
                best = _consider(pl[f], best)
        md = pl.get("metadata")
        if isinstance(md, dict):
            for f in TS_FIELDS:
                if f in md:
                    best = _consider(md[f], best)
    return datetime.fromtimestamp(best, tz=UTC) if best else None


def status_of(points: int, newest: datetime | None) -> str:
    if points == 0:
        return "⚪ empty"
    if newest is None:
        return "🟡 unverified"
    age_days = (datetime.now(tz=UTC) - newest).days
    if age_days > STALE_DAYS:
        return f"⚠️ historical-only ({age_days}d)"
    return "✅ live"


def describe(name: str) -> tuple[int, str, str, str]:
    pts = count(name)
    payloads = sample(name) if pts else []
    src_counts: dict[str, int] = {}
    for pl in payloads:
        src = str(pl.get("source") or pl.get("source_type") or "?")[:40]
        src_counts[src] = src_counts.get(src, 0) + 1
    top_src = (
        ", ".join(f"`{s}`" for s, _ in sorted(src_counts.items(), key=lambda kv: -kv[1])[:3]) or "—"
    )
    newest = newest_ts(payloads)
    fresh = newest.strftime("%Y-%m-%d") if newest else "—"
    return pts, top_src, fresh, status_of(pts, newest)


def render(rows: list[tuple[str, int, str, str, str]]) -> str:
    now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Collection Registry",
        "",
        f"> **AUTO-GENERATED** by `scripts/registry_report.py` — do not edit by hand. Last run: {now}.",
        "> Point counts are exact; sources and freshness are sampled (300 points).",
        "",
        "| Collection | Points | Top sources (sampled) | Newest (sampled) | Status |",
        "|------------|-------:|-----------------------|------------------|--------|",
    ]
    for name, pts, src, fresh, status in rows:
        lines.append(f"| `{name}` | {pts:,} | {src} | {fresh} | {status} |")
    total = sum(r[1] for r in rows)
    lines += ["", f"**{len(rows)} collections · {total:,} points total.**", "", OWNERSHIP_NOTES, ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="update docs/COLLECTION_REGISTRY.md")
    args = ap.parse_args()

    rows = []
    for name in collections():
        pts, src, fresh, status = describe(name)
        rows.append((name, pts, src, fresh, status))
        print(f"  {name:28} {pts:>9,}  {fresh:>10}  {status}")
    rows.sort(key=lambda r: -r[1])
    doc = render(rows)
    if args.write:
        DOC.write_text(doc)
        print(f"\nwrote {DOC}")
    else:
        print(f"\n(dry run — pass --write to update {DOC})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
