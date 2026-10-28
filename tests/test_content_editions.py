"""Golden tests for the three named editions.

No model, no network: the writer and editor are injected callables, exactly as
``write_edition`` accepts them. What is asserted is the contract a shipped
edition must satisfy — exact title, paragraph count inside the edition span, a
resolving citation on every paragraph, and a refusal when the notes are thin.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from rugbrain.content.editions import EDITIONS, edition
from rugbrain.content.image import choose_image
from rugbrain.content.messages import writer_system
from rugbrain.content.writer import write_edition

NOTES: dict[str, list[dict[str, Any]]] = {
    "morning": [
        {
            "title": "Velodrome pool drained after router approval bug",
            "publisher": "Rekt News",
            "url": "https://rekt.news/velodrome-router",
            "snippet": (
                "An attacker used a stale router approval to drain 2.4 million USD "
                "from three Velodrome pools on Optimism. The first withdrawal landed "
                "at 04:12 UTC."
            ),
        },
        {
            "title": "Optimism finality lagged during the drain window",
            "publisher": "L2Beat",
            "url": "https://l2beat.com/scaling/optimism",
            "snippet": (
                "Sequencer finality lagged 41 minutes between 03:40 and 04:21 UTC, "
                "the same window as the drain."
            ),
        },
        {
            "title": "Net bridge outflow overnight",
            "publisher": "Nansen",
            "url": "https://nansen.ai/research/bridge-flow",
            "snippet": (
                "Net bridge outflow reached 18,400 ETH overnight, the largest "
                "single-day move since May 2026."
            ),
        },
        {
            "title": "Scheduled supply unlock",
            "publisher": "Tokenomist",
            "url": "https://tokenomist.ai/unlocks",
            "snippet": "A 4.2% supply unlock is scheduled for October 30, 2026.",
        },
    ],
    "midday": [
        {
            "title": "Pendle fee-switch vote opens",
            "publisher": "CoinDesk",
            "url": "https://coindesk.com/markets/pendle-vote",
            "snippet": (
                "Pendle governance opened a fee-switch vote; the token moved 14.6% in the session."
            ),
        },
        {
            "title": "Perp open interest adds 310 million USD",
            "publisher": "Hyperliquid",
            "url": "https://hyperliquid.xyz/stats",
            "snippet": (
                "Perp open interest rose by 310 million USD, the largest add since August 2026."
            ),
        },
        {
            "title": "Funding flips negative",
            "publisher": "Velo Data",
            "url": "https://velo.xyz/funding",
            "snippet": "8-hour funding flipped negative at minus 0.004% on the same venue.",
        },
    ],
    "evening": [
        {
            "title": "Morning call resolved: drain stayed inside three pools",
            "publisher": "Rug Munch research desk",
            "url": "https://rugmunch.io/story/daily-rug-2026-10-29-optimism",
            "snippet": (
                "The morning call that the drain was contained to three pools held: "
                "no fourth pool was touched by 21:00 UTC."
            ),
        },
        {
            "title": "SOL daily close",
            "publisher": "CoinGecko",
            "url": "https://coingecko.com/en/coins/solana",
            "snippet": "SOL closed the day at 187.40 USD, down 3.1%.",
        },
        {
            "title": "DEX volume, day over day",
            "publisher": "DeFiLlama",
            "url": "https://defillama.com/dexs",
            "snippet": "DEX volume finished at 4.8 billion USD, 12% below the prior session.",
        },
        {
            "title": "Scheduled minutes release",
            "publisher": "Federal Reserve",
            "url": "https://federalreserve.gov/monetarypolicy/calendar",
            "snippet": "Minutes of the October meeting are scheduled for November 5, 2026.",
        },
    ],
}

DRAFTS: dict[str, dict[str, Any]] = {
    "morning": {
        "status": "ok",
        "headline": "Stale router approval drains 2.4 million USD from three Optimism pools",
        "dek": "A router approval bug and a sequencer delay landed in the same window.",
        "paragraphs": [
            "An attacker drained 2.4 million USD from three Velodrome pools on Optimism "
            "at 04:12 UTC using a stale router approval [1].",
            "Finality on the Optimism sequencer lagged 41 minutes across the same window, "
            "and bridge outflow reached 18,400 ETH overnight [2][3].",
            "The next scheduled test of the desk's read is a 4.2% supply unlock on "
            "October 30, 2026 [4].",
        ],
        "telegram_teaser": (
            "An attacker drained 2.4 million USD from three Velodrome pools while "
            "Optimism finality lagged 41 minutes."
        ),
    },
    "midday": {
        "status": "ok",
        "headline": "Pendle fee-switch vote lifts the token 14.6% in the session",
        "dek": "Open interest and funding moved on the same venue in the same window.",
        "paragraphs": [
            "Pendle moved 14.6% after governance opened a fee-switch vote [1].",
            "Perp open interest on Hyperliquid rose by 310 million USD in the same "
            "window, the largest add since August 2026 [2].",
            "Funding on that venue flipped negative for the first time in the session [3].",
        ],
        "telegram_teaser": (
            "Pendle is up 14.6% on a fee-switch vote while open interest added "
            "310 million USD on Hyperliquid."
        ),
    },
    "evening": {
        "status": "ok",
        "headline": "Solana closes at 187.40 USD as DEX volume falls 12%",
        "dek": "The desk's morning call held and the tape closed on lower volume.",
        "paragraphs": [
            "Solana closed the day at 187.40 USD, down 3.1% [2].",
            "DEX volume finished at 4.8 billion USD, 12% below the prior session [3].",
            "The desk's morning call held: no fourth Velodrome pool was touched by 21:00 UTC [1].",
            "The next scheduled catalyst in the notes is the minutes release on "
            "November 5, 2026 [4].",
        ],
        "telegram_teaser": (
            "Solana closed at 187.40 USD, down 3.1%, as DEX volume fell 12% on the day."
        ),
    },
}

IMAGE_CANDIDATES = [
    {
        "source_url": "https://cdn.rugmunch.io/img/velodrome-drain.png",
        "alt": "Velodrome pool drain transaction trace",
        "credit": "Rug Munch Media",
    },
    {
        "source_url": "https://cdn.rugmunch.io/img/logo.png",
        "alt": "Desk logo",
        "credit": "Rug Munch Media",
    },
]


@pytest.mark.parametrize("key", list(EDITIONS))
def test_edition_titles_and_send_times_are_exact(key: str) -> None:
    spec = edition(key)
    assert (
        spec["title"]
        == {
            "morning": "Rug Munch Intelligence — The Morning Rug",
            "midday": "Rug Munch Intelligence — Midday Market Report",
            "evening": "Rug Munch Intelligence — Evening Market Closing Report",
        }[key]
    )
    assert spec["send_utc"] == {"morning": "06:30", "midday": "12:30", "evening": "21:30"}[key]
    assert 2 <= spec["paragraphs"][0] <= spec["paragraphs"][1] <= 4


@pytest.mark.parametrize("key", list(EDITIONS))
async def test_golden_edition_is_publishable(key: str) -> None:
    prompts: list[str] = []

    async def fake_writer(system: str, user: str) -> str:
        prompts.append(system)
        assert edition(key)["title"] in system
        assert "$" not in system
        assert "[1]" in user
        return json.dumps({**DRAFTS[key], "title": "ignored — title is fixed"})

    async def fake_editor(system: str, user: str) -> str:
        return json.dumps({"verdict": "ok"})

    doc = await write_edition(
        key,
        NOTES[key],
        date="2026-10-29",
        story={"title": DRAFTS[key]["headline"]},
        image_candidates=IMAGE_CANDIDATES,
        writer=fake_writer,
        editor=fake_editor,
    )

    spec = edition(key)
    assert doc["status"] == "ok", doc["style_violations"]
    assert doc["title"] == spec["title"]
    assert doc["send_utc"] == spec["send_utc"]
    low, high = spec["paragraphs"]
    assert low <= len(doc["paragraphs"]) <= high
    source_ids = {row["id"] for row in doc["sources"]}
    for paragraph in doc["paragraphs"]:
        markers = {int(match) for match in re.findall(r"\[(\d{1,2})\]", paragraph)}
        assert markers, paragraph
        assert markers <= source_ids, (markers, source_ids)
    assert doc["sources"] and all(row["url"].startswith("https://") for row in doc["sources"])
    assert doc["image"]["status"] == "ok"
    assert doc["image"]["source_url"] in {row["source_url"] for row in IMAGE_CANDIDATES}
    assert 0 < doc["telegram"]["chars"] <= spec["teaser_chars"]
    assert doc["style_violations"] == []
    assert prompts, "the writer prompt was never rendered"


@pytest.mark.parametrize("key", ["morning", "midday", "evening"])
async def test_thin_evidence_refuses_without_calling_the_writer(key: str) -> None:
    called = False

    async def fake_writer(system: str, user: str) -> str:  # pragma: no cover - must not run
        nonlocal called
        called = True
        return json.dumps({"status": "ok", "paragraphs": []})

    doc = await write_edition(key, NOTES[key][:1], writer=fake_writer, date="2026-10-29")

    assert doc["status"] == "insufficient_evidence"
    assert doc["paragraphs"] == []
    assert "need 2" in doc["reason"]
    assert called is False


async def test_single_publisher_is_not_enough() -> None:
    same_domain = [
        {**row, "url": f"https://example.com/{index}"}
        for index, row in enumerate(NOTES["morning"][:3])
    ]
    doc = await write_edition("morning", same_domain, date="2026-10-29")
    assert doc["status"] == "insufficient_evidence"
    assert "independent publisher" in doc["reason"]


async def test_editor_strips_an_invented_figure_and_reports_it() -> None:
    invented = (
        "An attacker drained 2.4 million USD from three Velodrome pools and 5000 "
        "wallets were emptied [1]."
    )

    async def fake_writer(system: str, user: str) -> str:
        return json.dumps(DRAFTS["morning"])

    async def fake_editor(system: str, user: str) -> str:
        return json.dumps(
            {
                "verdict": "ok",
                "paragraphs": [invented, *DRAFTS["morning"]["paragraphs"][1:]],
                "stripped_claims": ["an unsupported claim about the deployer"],
                "style_violations": ["support: claim about a fourth pool is not in the notes"],
            }
        )

    doc = await write_edition(
        "morning",
        NOTES["morning"],
        date="2026-10-29",
        writer=fake_writer,
        editor=fake_editor,
    )

    assert doc["status"] == "needs_review"
    assert any("5000" in violation for violation in doc["style_violations"])
    assert any("editor:" in violation for violation in doc["style_violations"])
    assert "an unsupported claim about the deployer" in doc["stripped_claims"]
    assert all("5000" not in paragraph for paragraph in doc["paragraphs"])


def test_image_never_leaves_the_candidate_list() -> None:
    chosen = choose_image(
        {"title": "Velodrome router drain"},
        IMAGE_CANDIDATES,
        {"source_url": "https://evil.example/x.png", "alt": "made up", "credit": "nobody"},
    )
    assert chosen["status"] == "ok"
    assert chosen["source_url"] in {row["source_url"] for row in IMAGE_CANDIDATES}
    assert chosen["credit"] == "Rug Munch Media"


def test_image_falls_back_to_no_image() -> None:
    assert choose_image({"title": "anything"}, []) == {
        "status": "no_image",
        "reason": "no candidate carried url + alt + credit",
    }
    assert (
        choose_image({}, [{"source_url": "https://x/y.png", "alt": "no credit"}])["status"]
        == "no_image"
    )


def test_prompt_pack_renders_every_edition() -> None:
    for key in EDITIONS:
        system = writer_system(edition(key))
        assert "$" not in system
        assert edition(key)["title"] in system
        assert "insufficient_evidence" in system
