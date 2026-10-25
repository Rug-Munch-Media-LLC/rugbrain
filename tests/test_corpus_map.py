"""corpus_map shapes, honesty and the 40-line brief cap (no network)."""

from __future__ import annotations

import asyncio

from rugbrain.corpus_awareness import QdrantUnreachable, build_corpus_map

SAMPLES = {
    "news_v2": [{"title": "Hack drains $12M", "published_at": "2026-10-24T10:00:00Z"}],
    "defi_exploits": [{"title": "Reentrancy in vault"}, {"title": "Oracle manipulation"}],
    "emptydoc": [],
}


class FakeProbe:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    async def collections(self) -> list[str]:
        return self._names

    async def describe(self, name: str, samples: int) -> dict:
        rows = SAMPLES.get(name, [])
        return {"dim": 3072, "points": 0 if name == "emptydoc" else 1234, "samples": rows[:samples]}


class DeadProbe:
    async def collections(self) -> list[str]:
        raise QdrantUnreachable("http://127.0.0.1:6334: ConnectError")

    async def describe(self, name: str, samples: int) -> dict:
        raise QdrantUnreachable("unused")


def test_brief_is_capped_and_names_every_collection() -> None:
    result = asyncio.run(build_corpus_map(FakeProbe(["news_v2", "defi_exploits", "emptydoc"])))
    assert result["status"] == "ok"
    lines = result["text"].splitlines()
    assert len(lines) <= 40
    for name in ("news_v2", "defi_exploits", "emptydoc"):
        assert any(name in line for line in lines)
    assert "points" not in result


def test_empty_collection_is_marked_not_empty_list() -> None:
    result = asyncio.run(build_corpus_map(FakeProbe(["emptydoc"])))
    row = result["collections"][0]
    assert row["empty"] is True
    assert row["points"] == 0
    assert row["sample_titles"] == []
    assert result["status"] == "ok"


def test_full_adds_payload_keys_and_five_samples() -> None:
    result = asyncio.run(build_corpus_map(FakeProbe(["defi_exploits"]), detail="full"))
    row = result["collections"][0]
    assert row["payload_keys"] == ["title"]
    assert len(row["samples"]) == 2
    assert row["answers"].startswith("how was a DeFi protocol exploited")


def test_unreachable_qdrant_is_a_typed_refusal() -> None:
    result = asyncio.run(build_corpus_map(DeadProbe()))
    assert result["status"] == "refused"
    assert result["error"]["code"] == "qdrant_unreachable"
    assert result["collections"] == []
