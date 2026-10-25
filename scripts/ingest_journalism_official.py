#!/usr/bin/env python3
"""Ingest official US regulator + central-bank releases into the RAG corpus.

Facade over ``regs_ingest`` (config / feeds / normalise / store). Fills
``regulation_enforcement_8b`` with current primary sources a crypto-news writer
needs: SEC press + litigation, CFTC press, Federal Reserve press.
Licence: public domain (17 U.S.C. 105).

Usage:
  python scripts/ingest_journalism_official.py --limit 200
  python scripts/ingest_journalism_official.py --pages 3 --max-extra 300
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rugbrain.ingest_common import store_documents  # noqa: E402

from regs_ingest.config import COLLECTION, FEEDS, PROBE_QUERIES, UA  # noqa: E402
from regs_ingest.archives import archive_items  # noqa: E402
from regs_ingest.feeds import cftc_items, load_seed, paged_items, rss_items  # noqa: E402
from regs_ingest.normalise import (  # noqa: E402
    dedupe_text, dedupe_urls, fetch_page, to_documents,
)
from regs_ingest.store import (  # noqa: E402
    SiliconFlowEmbedder, build_store, count_points, existing_keys, probe,
)


async def collect(client, args, report) -> list[dict]:
    items: list[dict] = []
    per_feed = max(1, args.limit // len(FEEDS))
    for name, agency, url, cap in FEEDS:
        if name == "cftc_press":
            seed = load_seed(args.cftc_seed, report)
            got = seed or await cftc_items(client, report)
            report[f"{name}:rss"] = f"items={len(got)}"
            items.extend(got[: min(cap, per_feed)])
            continue
        items.extend(await rss_items(client, name, agency, url, min(cap, per_feed), report["feeds"]))
    if args.pages:
        extra = await paged_items(client, args.pages, args.max_extra, report)
        extra += await archive_items(client, args.max_extra, report)
        report["paged_candidates"] = len(extra)
        items.extend(extra)
    return items


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--pages", type=int, default=0)
    ap.add_argument("--max-extra", type=int, default=300)
    ap.add_argument("--cftc-seed", default="")
    ap.add_argument("--probe-only", action="store_true")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--report", default="/tmp/regs_report.json")
    args = ap.parse_args()

    embedder = SiliconFlowEmbedder()
    store = build_store()
    report: dict = {"feeds": {}, "stats": {}}
    report["before_count"] = await count_points()
    report["before"] = await probe(embedder, PROBE_QUERIES, [COLLECTION])
    if args.probe_only:
        json.dump(report, open(args.report, "w"), indent=1)
        print(json.dumps(report["before"], indent=1)[:4000])
        return 0

    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=45.0, headers={"User-Agent": UA}) as client:
        candidates = await collect(client, args, report)
        known_urls, shape = await existing_keys()
        report["existing"] = shape
        fresh, dup_url = dedupe_urls(candidates, known_urls)
        report["dedupe"] = {"candidates": len(candidates), "url_duplicates": dup_url,
                            "url_new": len(fresh)}
        if args.plan_only:
            by_feed: dict[str, int] = {}
            for it in fresh:
                by_feed[it["feed"]] = by_feed.get(it["feed"], 0) + 1
            report["new_by_feed"] = by_feed
            json.dump(report, open(args.report, "w"), indent=1)
            print("CANDIDATES", len(candidates), "URL_NEW", len(fresh), by_feed)
            print("CFTC", report.get("cftc", "-"))
            print("FEEDS", json.dumps(report["feeds"]))
            return 0
        filled = await asyncio.gather(*[fetch_page(client, it, sem) for it in fresh])
    ok = [f for f in filled if len(f.get("body") or "") >= 200]
    report["fetched"] = {"bodies": len(ok), "dropped": len(filled) - len(ok)}
    unique, dup_text = dedupe_text(ok)
    report["dedupe"]["text_duplicates"] = dup_text
    docs = to_documents(unique)
    report["docs"] = len(docs)
    report["stats"] = await store_documents(docs, embedder, store, chunk_size=2500, chunk_overlap=150)
    report["after_count"] = await count_points()
    report["after"] = await probe(embedder, PROBE_QUERIES, [COLLECTION])
    json.dump(report, open(args.report, "w"), indent=1)
    print("CFTC", report.get("cftc", "-"))
    print("DEDUPE", json.dumps(report["dedupe"]))
    print("DOCS", len(docs), "STATS", json.dumps(report["stats"]))
    print("POINTS", report["before_count"], "->", report["after_count"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
