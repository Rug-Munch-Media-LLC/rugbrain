"""corpus_ask: routing, merge/dedupe by story key, budget, honesty."""

from __future__ import annotations

import asyncio

from rugbrain.corpus_awareness import ask_corpus, route_question

HITS = {
    "news_v2": [
        {"score": 0.81, "payload": {"title": "Same story", "url": "https://x/1", "text": "a" * 400}},
        {"score": 0.42, "payload": {"title": "Other news", "url": "https://x/2", "text": "b" * 400}},
    ],
    "defi_exploits": [
        {"score": 0.77, "payload": {"title": "Same story", "url": "https://x/1", "text": "a" * 400}},
        {"score": 0.20, "payload": {"title": "Weak hit", "url": "https://x/3", "text": "c" * 400}},
    ],
}


class FakeSearcher:
    def __init__(self, table: dict[str, list[dict]]) -> None:
        self.table = table
        self.seen: list[str] = []

    async def search(self, collection: str, query: str, limit: int) -> list[dict]:
        self.seen.append(collection)
        return self.table.get(collection, [])


def test_news_question_routes_to_news_collection() -> None:
    assert route_question("what crypto news happened today", available=["news_v2"])[0] == "news_v2"


def test_merge_dedupes_by_story_key_and_cites() -> None:
    result = asyncio.run(
        ask_corpus("crypto news today", FakeSearcher(HITS), collections=["news_v2", "defi_exploits"])
    )
    assert result["status"] == "ok"
    titles = [c["title"] for c in result["citations"]]
    assert titles.count("Same story") == 1
    assert result["citations"][0]["score"] == 0.81
    assert "defi_exploits" in result["citations"][0]["also_in"]
    assert all(c["collection"] for c in result["citations"])


def test_token_budget_is_enforced() -> None:
    result = asyncio.run(
        ask_corpus(
            "exploit reentrancy news",
            FakeSearcher(HITS),
            budget_tokens=60,
            collections=["news_v2", "defi_exploits"],
        )
    )
    assert result["status"] == "ok"
    assert len(result["answer"]) <= 240
    assert result["below_budget"] is True


def test_unknown_question_is_insufficient_evidence() -> None:
    result = asyncio.run(
        ask_corpus("zorblax quux plughbar", FakeSearcher({}), collections=["news_v2"])
    )
    assert result["status"] == "insufficient_evidence"
    assert result["citations"] == []
    assert result["answer"] == ""


def test_search_lane_down_is_a_typed_refusal() -> None:
    class Dead:
        async def search(self, collection: str, query: str, limit: int) -> list[dict]:
            raise RuntimeError("connection refused")

    result = asyncio.run(ask_corpus("exploit", Dead(), collections=["defi_exploits"]))
    assert result["status"] == "refused"
    assert result["error"]["code"] == "search_unreachable"
