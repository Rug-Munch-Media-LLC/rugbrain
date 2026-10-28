#!/usr/bin/env python3
"""Measure value/dedupe routing on the fixture set against the LIVE lanes.

    python3 scripts/eval_content_value.py            # all four lanes
    CONTENT_EVAL_NO_RERANK=1 python3 scripts/eval_content_value.py

Prints every routing decision, the dedupe confusion matrix and its precision /
recall. The numbers quoted in ``docs/CONTENT-ROUTING.md`` come from this script;
if they disagree, the doc is stale, not the code.

Needs the Talos lanes: corpus embed 8B/4096 on ``:8004`` and the 0.6B rerank on
``:8030``. Runs on Talos only — a laptop has neither.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

from rugbrain.content_cream import cream_of_the_crop

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "content" / "value_fixtures.json"
EMBED_URL = os.getenv("CONTENT_CORPUS_EMBED_URL", "http://127.0.0.1:8004")
KEY_FILE = Path(os.getenv("CONTENT_LANE_KEY_FILE", "/srv/models/embed-lane.env"))
DAY = "2026-10-20"
NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)


def lane_key() -> str:
    """Bearer key for the local model lanes (env first, then the lane env file)."""
    for name in ("QWEN_EMBED_API_KEY", "LLAMA_EMBED_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    try:
        for line in KEY_FILE.read_text().splitlines():
            if line.startswith("LLAMA_EMBED_KEY="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


async def embed(texts: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=1200) as client:
        response = await client.post(
            f"{EMBED_URL}/v1/embeddings",
            headers={"Authorization": f"Bearer {lane_key()}"},
            json={"model": "qwen3-embedding-8b", "input": texts},
        )
    response.raise_for_status()
    return [item["embedding"] for item in response.json()["data"]]


def score(result: dict, truth: dict[str, str]) -> tuple[float, float]:
    """Precision/recall over PAIRS, plus the missed-duplicate count."""
    pairs = [(e["id"], e["representative_id"]) for e in result["suppressed"]]
    true_dup = sum(1 for a, b in pairs if truth[a] == truth[b])
    false_dup = len(pairs) - true_dup
    kept = sorted({pick["story"]["id"] for pick in result["picks"]})
    missed = sum(
        1
        for index, first in enumerate(kept)
        for second in kept[index + 1 :]
        if truth[first] == truth[second]
    )
    precision = true_dup / (true_dup + false_dup) if true_dup + false_dup else 1.0
    recall = true_dup / (true_dup + missed) if true_dup + missed else 1.0
    return precision, recall


async def main() -> None:
    items = json.loads(FIXTURE.read_text())["items"]
    result = await cream_of_the_crop(
        [dict(item, kind="story") for item in items],
        day=DAY,
        embed=embed,
        now=NOW,
        reorder=os.getenv("CONTENT_EVAL_NO_RERANK") != "1",
    )
    print("lanes:", result["lane"])
    print("\n== picks ==")
    for pick in result["picks"]:
        print(
            f"{pick['value']:.3f} {pick['story']['id']:20s} {pick['tier']:7s} "
            f"{pick['channel']:6s} route={pick['route']:7s} repeat={pick['repeat']}"
        )
    print("\n== suppressed ==")
    for entry in result["suppressed"]:
        print(
            f"{entry['id']:20s} -> {entry['representative_id']:20s} {entry['layer']:8s} "
            f"j={entry['jaccard']} cos={entry['cosine']}"
        )
    truth = {item["id"]: item["cluster_id"] for item in items}
    precision, recall = score(result, truth)
    print("\n== dedupe on fixtures ==")
    print(f"precision={precision:.3f} recall={recall:.3f}")
    print(f"stories kept: {len(result['picks'])} of {len(set(truth.values()))}")
    print("broadcast order:", [p["story"]["id"] for p in result["ordered"]])


if __name__ == "__main__":
    asyncio.run(main())
