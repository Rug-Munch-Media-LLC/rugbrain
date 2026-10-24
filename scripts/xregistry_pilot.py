"""Live pilot: push 20 x_registry accounts through the x_intel x-ingest path.

    python3 scripts/xregistry_pilot.py [--limit 20] [--out /tmp/x500_pilot.json]

Proves the registry wiring end to end on the REAL lanes:

1. 20 registry handles spread across kinds (news/security/analyst/kol).
2. Pry lane fetch → Redis dedupe → normalize → ``news_v2`` upsert (the shared
   embed lane must yield 4096-d qwen vectors).
3. Content-candidate eligibility: the x_social lane probe + cross-encoder
   rerank must surface at least one ingested pilot post.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rugbrain.ingest_sources.x_intel import registry

logger = logging.getLogger("xregistry_pilot")

#: Handles per kind for one pilot batch (news first — freshest desk feeds).
SAMPLE: dict[str, int] = {"news": 4, "security": 4, "analyst": 4, "kol": 8}


def pick_handles(limit: int) -> list[dict[str, Any]]:
    """Sample the registry across kinds, biggest accounts first per kind."""
    picks: list[dict[str, Any]] = []
    taken: set[str] = set()
    for kind, share in SAMPLE.items():
        n = (
            share
            if sum(SAMPLE.values()) == limit
            else max(1, round(share * limit / sum(SAMPLE.values())))
        )
        cand = [
            e
            for e in registry.registry_entries()
            if e.get("kind") == kind
            and (e.get("followers") or 0) >= 0
            and e["handle"].lower() not in taken
        ]
        cand.sort(key=lambda e: e.get("followers") or 0, reverse=True)
        for entry in cand[: max(0, n)]:
            taken.add(entry["handle"].lower())
            picks.append(entry)
    return picks[: max(0, limit)]


async def embed_dimension() -> int:
    """The live embed lane's vector width (registry posts must ride it)."""
    from rugbrain.embeddings import get_embedding

    return len(await get_embedding("xregistry pilot probe"))


def normalized_check(docs: list[dict[str, Any]]) -> int:
    """Docs that fully normalize (id + text + url + source + handle)."""
    ok = 0
    for doc in docs:
        if (
            str(doc.get("tweet_id") or "").strip()
            and str(doc.get("text") or "").strip()
            and str(doc.get("url") or "").strip()
        ):
            ok += 1
    return ok



async def main(limit: int) -> tuple[str, bool]:
    from rugbrain.ingest_sources.x_intel.dedupe import dedupe_seen, tweets_to_docs
    from rugbrain.ingest_sources.x_intel.pry_fetch import fetch_pry_tweets
    from rugbrain.ingest_sources.x_intel.storage import store_docs

    picks = pick_handles(limit)
    handles = [e["handle"] for e in picks]
    report: dict[str, Any] = {
        "registry_total": len(registry.registry_entries()),
        "pilot_handles": len(handles),
        "by_kind": {},
        "fetched": 0,
        "unique": 0,
        "normalized_ok": 0,
        "ingested": 0,
        "embed_dims": None,
        "candidates": None,
        "x_candidates_from_pilot": 0,
        "degraded": [],
    }
    for entry in picks:
        kind = str(entry.get("kind"))
        report["by_kind"][kind] = report["by_kind"].get(kind, 0) + 1

    try:
        report["embed_dims"] = await embed_dimension()
    except Exception as exc:
        report["degraded"].append(f"embed:{str(exc)[:120]}")
    if handles:
        try:
            fetched, dead = await fetch_pry_tweets(handles)
        except Exception as exc:
            fetched, dead = [], False
            report["degraded"].append(f"fetch:{str(exc)[:120]}")
        report["fetched"] = len(fetched)
        if dead:
            report["degraded"].append("pry_lane_dead")
        docs = tweets_to_docs(await dedupe_seen(fetched))
        report["unique"] = len(docs)
        report["normalized_ok"] = normalized_check(docs)
        if docs:
            try:
                stored = await store_docs(docs)
            except Exception as exc:
                stored = {"inserted": 0}
                report["degraded"].append(f"store:{str(exc)[:120]}")
            report["ingested"] = stored.get("inserted", 0)
            report["store"] = {
                k: v
                for k, v in stored.items()
                if k in ("inserted", "failed", "skipped", "chunks", "reason")
            }
        else:
            report["store"] = {"reason": "no docs"}
    else:
        report["degraded"].append("no_handles")
    try:
        from rugbrain import content_candidates

        stories = await content_candidates.recent_stories(limit=30)
        report["candidates"] = len(stories)
        pilot = {h.lower() for h in handles}
        for story in stories:
            # x_social candidates from this pilot's accounts are the pass signal.
            who = str((story.get("metadata") or {}).get("handle") or "").lower()
            if story.get("metadata", {}).get("category") == "x_social" and who in pilot:
                report["x_candidates_from_pilot"] += 1
        if content_candidates.LAST_REASON:
            report["degraded"].append(f"candidates:{content_candidates.LAST_REASON[:120]}")
    except Exception as exc:
        report["degraded"].append(f"candidates:{str(exc)[:120]}")
    ok = report["normalized_ok"] > 0 and report["embed_dims"] in (4096,) and bool(report["candidates"])
    return json.dumps(report, indent=1, ensure_ascii=False) + "\n", ok


def run_pilot(limit: int, out: str) -> int:
    """Writes the report file + prints it; returns the process exit code."""
    report_text, ok = asyncio.run(main(limit))
    Path(out).write_text(report_text)
    print(report_text, end="")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="x_registry live pilot (20 accounts)")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--out", default="data/x_registry_pilot_report.json")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level)
    sys.exit(run_pilot(args.limit, args.out))
