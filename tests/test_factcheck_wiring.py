"""Wiring tests: the fact-check gate is on the content-engine draft path.

Proves the gate is actually invoked by ``_compose_with_factcheck`` (the
function ``generate_channel_drafts`` routes every draft through), not merely
importable. A draft with an uncorroborated claim is blocked; a corroborated
draft passes; the config flag bypasses the gate.
"""

from __future__ import annotations

import pytest

from rugbrain import content_engine

#: A draft whose only verifiable claim (address + amount) is corroborated by
#: two independent domains — the gate must PASS.
GOOD_STORY = {
    "source": "reuters.com",
    "url": "https://reuters.com/story",
    "content": "Attackers drained $12.4m from 0x1111111111111111111111111111111111111111.",
    "sources": [
        {
            "url": "https://coindesk.com/story",
            "content": "The protocol lost $12.4m; 0x1111111111111111111111111111111111111111 flagged.",
        }
    ],
}

#: A draft whose claim appears in NO source — the gate must FAIL (block).
BAD_STORY = {
    "source": "randomblog.example",
    "url": "https://randomblog.example/post",
    "content": "Rumor: funds moved to 0x9999999999999999999999999999999999999999.",
}


@pytest.fixture
def _stub_compose(monkeypatch: pytest.MonkeyPatch):
    """Replace the LLM writer with a fixed draft so the test is offline."""

    def _install(text: str) -> None:
        async def _fake(story: dict) -> tuple[str, str]:
            return text, "llm"

        monkeypatch.setattr(content_engine, "_compose", _fake)

    return _install


async def test_bad_claim_is_blocked(_stub_compose):
    _stub_compose(BAD_STORY["content"])
    text, writer = await content_engine._compose_with_factcheck(BAD_STORY)
    assert writer == "rejected"
    assert text == ""


async def test_good_draft_passes(_stub_compose):
    _stub_compose(GOOD_STORY["content"])
    text, writer = await content_engine._compose_with_factcheck(GOOD_STORY)
    assert writer == "llm"
    assert text  # draft survives
    assert "rejected" not in writer


async def test_flag_off_bypasses_gate(_stub_compose, monkeypatch: pytest.MonkeyPatch):
    _stub_compose(BAD_STORY["content"])
    monkeypatch.setattr(content_engine, "FACTCHECK_ENABLED", False)
    text, writer = await content_engine._compose_with_factcheck(BAD_STORY)
    assert writer == "llm"
    assert text == BAD_STORY["content"]


async def test_opinion_draft_passes_through(_stub_compose):
    """No verifiable claims -> SKIP -> pass through unchanged."""
    _stub_compose("The market feels uncertain this week.")
    text, writer = await content_engine._compose_with_factcheck(
        {"source": "op-ed", "url": "https://x.com/a", "content": "The market feels uncertain."}
    )
    assert writer == "llm"
    assert text == "The market feels uncertain this week."


def test_gate_is_wired_into_draft_path():
    """The draft path must route through the gate, not call _compose directly."""
    import inspect

    src = inspect.getsource(content_engine.generate_channel_drafts)
    assert "_compose_with_factcheck" in src
