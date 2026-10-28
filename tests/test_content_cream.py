"""End-to-end: tri-dedupe → value → route → rerank-lane ordering, with fakes.

Both live lanes are injected, so this runs in CI without a network. The
fixture set has 6 true stories across 12 items; a correct pass returns exactly
one pick per story and reports the other six as suppressed with the layer that
caught each.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from rugbrain.content_cream import cream_of_the_crop
from rugbrain.content_history import SeenLedger

FIXTURE = Path(__file__).parent / "fixtures" / "content" / "value_fixtures.json"
ITEMS = json.loads(FIXTURE.read_text())["items"]
CLUSTERS = sorted({item["cluster_id"] for item in ITEMS})
DAY = "2026-10-20"


def _story(item: dict[str, Any]) -> dict[str, Any]:
    story = dict(item)
    story.setdefault("kind", "story")
    return story


def _fake_vectors() -> list[list[float]]:
    """Members of one cluster sit at cosine ~0.98; different clusters are 0."""
    vectors: list[list[float]] = []
    step = 0.15
    for item in ITEMS:
        index = CLUSTERS.index(item["cluster_id"])
        vector = [0.0] * len(CLUSTERS)
        vector[index] = 1.0
        vector[(index + 1) % len(CLUSTERS)] = step
        norm = math.sqrt(sum(v * v for v in vector))
        vectors.append([v / norm for v in vector])
    return vectors


async def _embed(texts: list[str]) -> list[list[float]]:
    assert len(texts) == len(ITEMS)
    return _fake_vectors()


async def _order(stories: list[dict[str, Any]], **_kwargs: Any) -> list[dict[str, Any]]:
    ordered = sorted(stories, key=lambda s: str(s.get("id")), reverse=True)
    total = len(ordered)
    return [
        {
            **story,
            "rerank_score": float(total - index),
            "rerank_share": round(1.0 / total, 6),
            "rerank_method": "fake-lane",
        }
        for index, story in enumerate(ordered)
    ]


async def test_one_pick_per_story_with_all_three_layers() -> None:
    result = await cream_of_the_crop(
        [_story(item) for item in ITEMS], day=DAY, embed=_embed, order=_order
    )
    assert len(result["picks"]) == len(CLUSTERS) == 6
    assert len(result["suppressed"]) == 6
    assert {entry["layer"] for entry in result["suppressed"]} == {"exact", "near", "semantic"}
    assert {pick["story"]["cluster_id"] for pick in result["picks"]} == set(CLUSTERS)


async def test_suppressed_copies_point_at_their_representative() -> None:
    result = await cream_of_the_crop(
        [_story(item) for item in ITEMS], day=DAY, embed=_embed, order=_order
    )
    kept_ids = {pick["story"]["id"] for pick in result["picks"]}
    for entry in result["suppressed"]:
        assert entry["representative_id"] in kept_ids
        assert entry["id"] not in kept_ids


async def test_every_pick_carries_an_explainable_score() -> None:
    result = await cream_of_the_crop(
        [_story(item) for item in ITEMS], day=DAY, embed=_embed, order=_order
    )
    for pick in result["picks"]:
        breakdown = pick["value_breakdown"]
        assert set(breakdown["components"])
        assert breakdown["dedupe_penalty"]["weight"] > 0
        assert 0.0 <= pick["value"] <= 1.0
        assert pick["reason"]


async def test_rerank_lane_orders_only_what_earned_a_slot() -> None:
    result = await cream_of_the_crop(
        [_story(item) for item in ITEMS], day=DAY, embed=_embed, order=_order
    )
    assert result["lane"] == {"embed": "ok", "order": "fake-lane"}
    assert result["ordered"]
    ids = [pick["story"]["id"] for pick in result["ordered"]]
    assert ids == sorted(ids, reverse=True)  # the fake lane's order, not value order
    assert all(pick["route"] in ("channel", "both") for pick in result["ordered"])


async def test_published_story_comes_back_as_a_feed_update() -> None:
    seen = SeenLedger()
    kelp = next(item for item in ITEMS if item["id"] == "kelp-tg")
    seen.remember(_story(kelp), channel="news", day="2026-10-19")
    result = await cream_of_the_crop(
        [_story(kelp), _story(next(i for i in ITEMS if i["id"] == "eth-pq"))],
        day=DAY,
        seen=seen,
        embed=_embed,
        order=_order,
    )
    kelp_pick = next(p for p in result["picks"] if p["story"]["id"] == "kelp-tg")
    assert kelp_pick["repeat"] is not None
    assert kelp_pick["route"] == "feed"
    assert kelp_pick["channel"] == "site"


async def test_embed_lane_failure_degrades_to_two_layers() -> None:
    async def _dead(_texts: list[str]) -> list[list[float]]:
        raise RuntimeError("lane down")

    result = await cream_of_the_crop(
        [_story(item) for item in ITEMS], day=DAY, embed=_dead, order=_order
    )
    assert result["lane"]["embed"].startswith("embed_unavailable")
    # The two paraphrase-only copies (mango-rss, whale-x) survive without the
    # semantic layer: 8 picks, not 6. Degradation is visible, not silent.
    assert len(result["picks"]) == 8
