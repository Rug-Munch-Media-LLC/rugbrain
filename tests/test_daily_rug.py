"""Daily Rug generator: schema, citations, writer guard and Telegram summary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rugbrain.content_style import load_style
from rugbrain.daily_rug import generate_daily_rug, validate_daily_rug
from rugbrain.daily_rug_validate import REQUIRED_KEYS

FIXTURES = Path("tests/fixtures/content_style")
HITS: list[dict[str, Any]] = json.loads((FIXTURES / "evidence_bridge.json").read_text())
GOLDEN_TELEGRAM = (FIXTURES / "golden_telegram.txt").read_text()
GOLDEN_STRUCTURE = json.loads((FIXTURES / "golden_structure.json").read_text())
STYLE = load_style()


async def _search(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return HITS


async def _build(writer: Any = None) -> dict[str, Any]:
    return await generate_daily_rug(
        "bridge key compromise drain", date="2026-10-24", search_fn=_search, writer=writer
    )


@pytest.mark.asyncio
async def test_brief_matches_the_golden_structure() -> None:
    doc = await _build()
    assert doc["status"] == "ok", doc.get("style_violations")
    assert doc["slug"] == GOLDEN_STRUCTURE["slug"]
    assert doc["url"] == GOLDEN_STRUCTURE["url"] == f"https://rugmunch.io/story/{doc['slug']}"
    assert [s["heading"] for s in doc["sections"]] == GOLDEN_STRUCTURE["headings"]
    assert [s["id"] for s in doc["sources"]] == GOLDEN_STRUCTURE["sources"]
    for key in REQUIRED_KEYS:
        assert key in doc


@pytest.mark.asyncio
async def test_every_section_is_cited_and_resolves() -> None:
    doc = await _build()
    ids = {row["id"] for row in doc["sources"]}
    for section in doc["sections"]:
        markers = [int(m) for m in __import__("re").findall(r"\[(\d+)\]", section["body"])]
        assert markers, section["heading"]
        assert set(markers) <= ids


@pytest.mark.asyncio
async def test_telegram_summary_matches_golden_and_fits() -> None:
    doc = await _build()
    assert doc["telegram"]["text"] + "\n" == GOLDEN_TELEGRAM
    assert doc["telegram"]["chars"] < STYLE["length_budgets"]["telegram_hard_limit"]
    assert doc["url"] in doc["telegram"]["text"]
    assert STYLE["cta"]["url"] in doc["telegram"]["text"]


@pytest.mark.asyncio
async def test_open_graph_and_disclaimer_are_canonical() -> None:
    doc = await _build()
    assert doc["open_graph"]["og:url"] == doc["url"]
    assert doc["open_graph"]["og:type"] == "article"
    assert doc["open_graph"]["article:published_time"].startswith(doc["date"])
    assert doc["disclaimer"] == STYLE["disclaimers"]["daily_rug"]
    assert doc["cta"] == STYLE["cta"]


@pytest.mark.asyncio
async def test_one_source_returns_insufficient_evidence() -> None:
    async def thin(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return HITS[:1]

    doc = await generate_daily_rug("bridge drain", search_fn=thin)
    assert doc["status"] == "insufficient_evidence"
    assert "need 2" in doc["reason"]
    assert "sections" not in doc


@pytest.mark.asyncio
async def test_syndicated_copies_do_not_count_as_two_sources() -> None:
    syndicated = [HITS[0], {**HITS[1], "metadata": {**HITS[1]["metadata"], "url": "https://explorer.example/copy"}}]

    async def same_publisher(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return syndicated

    doc = await generate_daily_rug("bridge drain", search_fn=same_publisher)
    assert doc["status"] == "insufficient_evidence"
    assert "independent publisher" in doc["reason"]


@pytest.mark.asyncio
async def test_hit_without_url_is_not_citable() -> None:
    async def no_url(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"content": "Unverifiable claim", "metadata": {}}]

    doc = await generate_daily_rug("bridge drain", search_fn=no_url)
    assert doc["status"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_model_writer_with_invented_number_is_rejected() -> None:
    sections = [
        {"heading": f"Section {i}", "body": f"According to explorer.example, the loss was $900M [{i}]."}
        for i in range(1, 5)
    ]

    async def writer(system: str, user: str) -> str:
        return json.dumps({"title": "T", "dek": "D", "sections": sections})

    doc = await _build(writer)
    assert doc["status"] == "ok"
    assert doc["writer"]["mode"] == "deterministic"
    assert doc["writer"]["rejected"]


@pytest.mark.asyncio
async def test_model_writer_with_unresolved_citation_is_rejected() -> None:
    sections = [{"heading": f"S{i}", "body": f"According to a source, this happened [{i + 8}]."} for i in range(1, 5)]

    async def writer(system: str, user: str) -> str:
        return json.dumps({"sections": sections})

    doc = await _build(writer)
    assert doc["writer"]["mode"] == "deterministic"


@pytest.mark.asyncio
async def test_grounded_model_draft_is_accepted() -> None:
    sections = [
        {"heading": "What happened", "body": "According to explorer.example, a bridge lost $5M [1]."},
        {"heading": "Response", "body": "According to protocol.example, withdrawals were paused [2]."},
        {"heading": "Cause", "body": "According to postmortem.example, a deployer key leaked [3]."},
        {"heading": "Open questions", "body": "Total loss and recovery remain undocumented [1]."},
    ]

    async def writer(system: str, user: str) -> str:
        assert "to the moon" in system
        return json.dumps({"title": "T", "dek": "D", "sections": sections})

    doc = await _build(writer)
    assert doc["writer"]["mode"] == "model"
    assert doc["sections"][0]["heading"] == "What happened"


@pytest.mark.asyncio
async def test_validator_flags_a_section_without_citation() -> None:
    doc = await _build()
    doc["sections"][0]["body"] = "No marker here."
    assert any("no [n] marker" in problem for problem in validate_daily_rug(doc))


@pytest.mark.asyncio
async def test_validator_flags_a_long_title() -> None:
    doc = await _build()
    doc["title"] = "T" * 90
    assert any("title over headline budget" in problem for problem in validate_daily_rug(doc))
