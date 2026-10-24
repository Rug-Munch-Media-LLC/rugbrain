"""Golden tests for the canonical content style, gate and prompt pack."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rugbrain.content_prompts import load_prompts, render, surfaces, system_prompt
from rugbrain.content_style import budget, load_style
from rugbrain.content_style_lint import lint_text, normalise_text

STYLE = load_style()
GOLDEN = Path("tests/fixtures/content_style/golden_telegram.txt").read_text()

CLEAN = (
    "An attacker drained $5M from a bridge on Ethereum [1].\n\n"
    "The team paused withdrawals in the same hour [2].\n\n"
    "Sources\n\n"
    "[1] Report — explorer.example, October 24, 2026. https://explorer.example/tx/0xabc\n"
    "[2] Update — protocol.example, October 24, 2026. https://protocol.example/status/17"
)


def test_style_has_exactly_one_canonical_config() -> None:
    """A second style file would be a competing source of truth."""
    found = sorted(str(p) for p in Path(".").rglob("content_style.json"))
    assert found == ["config/content_style.json"]
    assert STYLE["canonical"] is True


@pytest.mark.parametrize(
    "group",
    ["voice", "tense", "capitalization", "emoji", "spacing", "numbers",
     "citations", "disclaimers", "length_budgets", "slug", "open_graph", "cta"],
)
def test_style_carries_every_rule_group(group: str) -> None:
    assert group in STYLE


@pytest.mark.parametrize(
    "surface", ["telegram_short", "telegram_thread", "feed_longform", "x_post", "alert", "daily_rug"]
)
def test_every_surface_renders_under_its_budget(surface: str) -> None:
    prompt = render(surface, topic="bridge drain", evidence="[1] Report — a.example. https://a.example")
    assert f"Budget: {budget(_key(surface))}" in prompt
    assert "[1] Report" in prompt


def _key(surface: str) -> str:
    return str(load_prompts()["surfaces"][surface]["budget"])


def test_prompt_pack_holds_no_style_rules_of_its_own() -> None:
    """Style words live in one file; a copy here would drift."""
    raw = json.dumps(load_prompts()).lower()
    for phrase in ("to the moon", "guaranteed", "first person", "sentence case"):
        assert phrase not in raw


def test_system_prompt_carries_style_and_required_footer() -> None:
    prompt = system_prompt("daily_rug")
    assert "to the moon" in prompt
    assert STYLE["disclaimers"]["daily_rug"] in prompt
    assert STYLE["disclaimers"]["daily_rug"] not in system_prompt("x_post")


def test_surfaces_are_the_six_published_ones() -> None:
    assert surfaces() == ("alert", "daily_rug", "feed_longform", "telegram_short", "telegram_thread", "x_post")


def test_normalise_repairs_spacing_only() -> None:
    messy = "First  line.  \n\n\n\nSecond line.\t\n"
    assert normalise_text(messy) == "First line.\n\nSecond line."
    assert normalise_text("Price is $5M") == "Price is $5M"


def test_clean_copy_passes_the_gate() -> None:
    assert lint_text(CLEAN, "feed_longform", source_count=2) == []


@pytest.mark.parametrize(
    ("text", "surface", "expected"),
    [
        ("Two  spaces here.", "x_post", "spacing: double space"),
        ("One.\n\n\nTwo.", "x_post", "spacing: more than one blank line"),
        ("This is insane growth.", "x_post", "voice: banned phrase"),
        ("The token will reach $10.", "x_post", "voice: price prediction"),
        ("Bridge drained 🚀 today.", "x_post", "emoji: not in allowlist"),
        ("🚨 Breach 🚨 confirmed.", "x_post", "emoji: 2 used"),
        ("MASSIVE breach confirmed.", "x_post", "capitalization: all-caps"),
        ("Fees rose 5 percent.", "x_post", "numbers: percent spelled out"),
        ("It happened October 24th.", "x_post", "numbers: ordinal date"),
        ("Losses were about $5M.", "x_post", "numbers: approximate quantity"),
        ("I think the pool is empty.", "x_post", "voice: first person singular"),
        ("Bridge drained!", "x_post", "voice: exclamation mark"),
        ("x" * 300, "x_post", "length: 300 chars exceeds x_post budget"),
        ("A claim with no proof [3].", "feed_longform", "citations: marker [3] has no source"),
    ],
)
def test_gate_rejects(text: str, surface: str, expected: str) -> None:
    problems = lint_text(text, surface, source_count=2 if "[" in text else None)
    assert any(expected in problem for problem in problems), problems


def test_golden_telegram_passes_the_short_surface() -> None:
    assert lint_text(GOLDEN, "telegram_short") == []
