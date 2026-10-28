"""Regression: the refusal-vs-ok boundary on real-shaped Telegram notes.

Born from a live run that ended ``needs_review`` with the bare reason
``writer: insufficient_evidence``: the model had refused with a reason, but
``_document`` dropped it, so the operator saw nothing to act on. These tests pin
both halves of that boundary — a refusal keeps its reason, and two notes from
two t.me channels are treated as a sufficient brief.
"""

from __future__ import annotations

import json

import pytest

from rugbrain.content.writer import write_edition
from rugbrain.content_daily.adapters import _normalise_written, edit_document

# The real shape: two Telegram channels, one shared domain, thin snippets.
TWO_CHANNELS: list[dict[str, object]] = [
    {
        "title": "predict-fun-exploit",
        "publisher": "t.me",
        "url": "https://t.me/news_crypto/25207",
        "snippet": "JUST IN: Predict Fun on BSC likely exploited, approx 300K in funds moving.",
    },
    {
        "title": "bitcoin-crash-warning",
        "publisher": "t.me",
        "url": "https://t.me/cryptorugmuncher/8095",
        "snippet": "A Bitcoin crash warning flags Monday risk while Bitcoin Cash gains.",
    },
]


async def test_model_refusal_reason_reaches_the_operator() -> None:
    async def refusing_writer(system: str, user: str) -> str:
        return json.dumps(
            {"status": "insufficient_evidence", "reason": "the notes state no figure"}
        )

    doc = await write_edition("morning", TWO_CHANNELS, date="2026-10-29", writer=refusing_writer)

    assert doc["status"] == "insufficient_evidence"
    assert doc["reason"] == "the notes state no figure"
    problems = edit_document(doc)
    assert problems == ["writer: insufficient_evidence: the notes state no figure"]


async def test_two_telegram_channels_are_sufficient_and_write_the_edition() -> None:
    called = False

    async def ok_writer(system: str, user: str) -> str:
        nonlocal called
        called = True
        return json.dumps(
            {
                "status": "ok",
                "headline": "Predict Fun on BSC likely exploited with 300K moving",
                "dek": "A single channel reported the BSC exploit overnight.",
                "paragraphs": [
                    "Just in: reported that Predict Fun on BSC is likely exploited "
                    "with approx 300K in funds moving [1].",
                    "A Bitcoin crash warning flagged Monday risk while Bitcoin Cash gained [2].",
                    "The desk read the two channels as independent: different t.me "
                    "handles, different publishers [1][2].",
                    "No image accompanied either note, so the edition carries text only [1][2].",
                ],
                "telegram_teaser": "Predict Fun on BSC is likely exploited with 300K moving.",
                "image": {"status": "no_image"},
            }
        )

    async def ok_editor(system: str, user: str) -> str:
        return json.dumps({"verdict": "ok"})

    doc = await write_edition(
        "morning",
        TWO_CHANNELS,
        date="2026-10-29",
        writer=ok_writer,
        editor=ok_editor,
    )

    assert called is True, "two t.me channels must not be refused before the writer runs"
    assert doc["status"] == "ok", doc["style_violations"]
    assert "reason" not in doc, "an ok edition carries no refusal reason"
    assert 3 <= len(doc["paragraphs"]) <= 4
    assert len({row["url"] for row in doc["sources"]}) == 2
    assert doc["image"]["status"] == "no_image"
    assert edit_document(_normalise_written("morning", "2026-10-29", doc)) == []


@pytest.mark.parametrize("note", TWO_CHANNELS)
async def test_a_single_channel_is_still_below_the_floor(note: dict[str, object]) -> None:
    async def must_not_run(system: str, user: str) -> str:  # pragma: no cover
        raise AssertionError("the writer must not run below the source floor")

    doc = await write_edition("morning", [note], date="2026-10-29", writer=must_not_run)

    assert doc["status"] == "insufficient_evidence"
    assert "need 2" in doc["reason"]
