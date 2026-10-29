"""Tri-layer dedupe: exact hash, shingle Jaccard, corpus-lane cosine.

The fixture set (``tests/fixtures/content/value_fixtures.json``) carries
ground-truth ``cluster_id``, so these tests assert the editorial fact — one
story, one post — not an internal shape.

The semantic THRESHOLD was measured on the live 8B/4096 lane (see
:mod:`rugbrain.content_dedupe_layers`); the hermetic tests reproduce those
cosines synthetically, and ``RAG_LIVE_LANES=1`` proves the real model.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from rugbrain.content_dedupe import collapse, collapse_with_report
from rugbrain.content_dedupe_layers import (
    EXACT,
    NEAR,
    SEMANTIC,
    SEMANTIC_THRESHOLD,
    cosine,
    layers_hit,
)
from rugbrain.content_history import SeenLedger

FIXTURE = Path(__file__).parent / "fixtures" / "content" / "value_fixtures.json"
ITEMS = json.loads(FIXTURE.read_text())["items"]
BY_ID = {item["id"]: item for item in ITEMS}

MEASURED = {"mango-rss": 0.6238, "whale-x": 0.8927}


def story(item_id: str) -> dict[str, object]:
    """Fixture item → normalised story dict."""
    item = dict(BY_ID[item_id])
    item.setdefault("kind", "story")
    return item


def _pair(cos_value: float) -> tuple[list[float], list[float]]:
    """Two unit vectors separated by exactly ``cos_value``."""
    return [1.0, 0.0], [cos_value, math.sqrt(max(0.0, 1.0 - cos_value**2))]


def _vectors(cos_value: float, *ids: str) -> dict[str, list[float]]:
    left, right = _pair(cos_value)
    return {ids[0]: left, ids[1]: right}


def test_exact_layer_survives_emoji_mention_and_case() -> None:
    """Normalisation is the cheapest false-positive-free win: an emoji, an
    @mention, a ticker case change and a link all vanish before hashing."""
    kept, suppressed = collapse_with_report([story("kelp-tg"), story("kelp-alt")])
    assert len(kept) == 1
    assert suppressed[0]["layer"] == EXACT


def test_near_layer_suppresses_light_edits() -> None:
    kept, suppressed = collapse_with_report([story("sol-outage"), story("sol-outage-stale")])
    assert len(kept) == 1
    assert suppressed[0]["layer"] == NEAR
    assert 0.62 <= float(suppressed[0]["jaccard"] or 0) < 1.0


def test_semantic_layer_suppresses_paraphrase() -> None:
    pair = ("mango-tg", "mango-rss")
    proof = layers_hit(story(pair[0]), story(pair[1]), cosine_value=MEASURED["mango-rss"])
    assert proof["layer"] == SEMANTIC
    kept, suppressed = collapse_with_report(
        [story(pair[0]), story(pair[1])], vectors=_vectors(MEASURED["mango-rss"], *pair)
    )
    assert len(kept) == 1
    assert suppressed[0]["layer"] == SEMANTIC


def test_semantic_floor_keeps_distinct_stories() -> None:
    """The strongest cross-cluster cosine measured (0.5500) must NOT merge."""
    left, right = story("mango-rss"), story("kelp-tg")
    proof = layers_hit(left, right, cosine_value=0.5500)
    assert proof["layer"] is None
    kept, suppressed = collapse_with_report(
        [left, right], vectors=_vectors(0.5500, "mango-rss", "kelp-tg")
    )
    assert len(kept) == 2
    assert suppressed == []


def test_novel_story_passes() -> None:
    kept, suppressed = collapse_with_report(
        [story("mango-tg"), story("eth-pq"), story("btc-funding")]
    )
    assert {item["id"] for item in kept} == {"eth-pq", "btc-funding", "mango-tg"}
    assert suppressed == []


@pytest.mark.parametrize(
    ("duplicate", "layer"),
    [
        ("mango-x", EXACT),  # layer (a)
        ("sol-outage-stale", NEAR),  # layer (b)
        ("whale-x", SEMANTIC),  # layer (c)
    ],
)
def test_three_duplicate_fixtures_are_suppressed(duplicate: str, layer: str) -> None:
    representative = {
        "mango-x": "mango-tg",
        "sol-outage-stale": "sol-outage",
        "whale-x": "whale-move",
    }[duplicate]
    cos_value = MEASURED.get(duplicate, 0.95)
    kept, suppressed = collapse_with_report(
        [story(representative), story(duplicate)],
        vectors=_vectors(cos_value, representative, duplicate),
    )
    assert len(kept) == 1
    assert {item["cluster_id"] for item in kept} == {BY_ID[duplicate]["cluster_id"]}
    assert suppressed[0]["layer"] == layer
    assert suppressed[0]["representative_id"] == kept[0]["id"]


def test_one_story_one_post_cross_channel() -> None:
    """The same wire story in three channels collapses to one, with corroboration."""
    vectors = _vectors(MEASURED["mango-rss"], "mango-tg", "mango-rss")
    vectors["mango-x"] = vectors["mango-tg"]
    kept = collapse([story("mango-tg"), story("mango-x"), story("mango-rss")], vectors=vectors)
    assert len(kept) == 1
    assert kept[0]["corroboration"] == 3
    trio = (story("mango-tg"), story("mango-x"), story("mango-rss"))
    assert set(kept[0]["also_seen_in"]) == {item["source"] for item in trio} - {kept[0]["source"]}


def test_cross_day_repeat_is_detected() -> None:
    ledger = SeenLedger()
    ledger.remember(story("kelp-tg"), channel="news", day="2026-10-20")
    repeat = ledger.repeat_of(story("kelp-day2"))
    assert repeat is not None
    assert repeat["layer"] in (EXACT, NEAR, SEMANTIC)


def test_same_item_not_posted_twice_in_one_day() -> None:
    ledger = SeenLedger()
    ledger.remember(story("sol-outage"), channel="news", day="2026-10-20")
    assert ledger.repeat_of(story("sol-outage-stale")) is not None
    assert ledger.posted("news", "2026-10-20") == 1
    assert ledger.posted("news", "2026-10-21") == 0


LIVE_EMBED_URL = os.getenv("CONTENT_CORPUS_EMBED_URL", "http://127.0.0.1:8004")


def _live_key() -> str:
    """Key for the local corpus lane; the lane accepts its own sentinel."""
    for name in ("QWEN_EMBED_API_KEY", "LLAMA_EMBED_KEY"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    try:
        for line in Path("/srv/models/embed-lane.env").read_text().splitlines():
            if line.startswith("LLAMA_EMBED_KEY="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


async def _live_embed(texts: list[str]) -> list[list[float]]:
    """Embed on the corpus 8B/4096 lane — the lane the threshold was measured on.

    Direct rather than through ``rugbrain.embeddings``: that chain's *env
    default* points at ``:8024``, now the 0.6B/1024 supermemory lane, so the
    width guard refuses every vector. Measure the LANE, not the drift.
    """
    import httpx

    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(
            f"{LIVE_EMBED_URL}/v1/embeddings",
            headers={"Authorization": f"Bearer {_live_key()}"},
            json={"model": "qwen3-embedding-8b", "input": texts},
        )
    response.raise_for_status()
    return [item["embedding"] for item in response.json()["data"]]


@pytest.mark.skipif(os.getenv("RAG_LIVE_LANES") != "1", reason="live lane check (RAG_LIVE_LANES=1)")
async def test_live_semantic_layer_matches_calibration() -> None:
    """Re-measure the 8B/4096 lane: the constant must still sit in the gap."""
    ids = ["mango-tg", "mango-rss", "kelp-tg", "whale-move", "eth-pq"]
    texts = [f"{BY_ID[i]['title']}\n{BY_ID[i]['content']}" for i in ids]
    vectors = await _live_embed(texts)
    got = {ids[i]: cosine(vectors[0], vectors[i]) for i in range(1, len(ids))}
    assert got["mango-rss"] >= SEMANTIC_THRESHOLD  # paraphrase must merge
    assert got["eth-pq"] < SEMANTIC_THRESHOLD  # unrelated must not
    assert got["kelp-tg"] < SEMANTIC_THRESHOLD
