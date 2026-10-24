"""The content candidate pool is ONE pool, whatever the source family.

Regression contract for the pipeline order documented in
``docs/CONTENT-PIPELINE.md``: every writer (Telegram web, Telegram
userbot/alt, X, RSS/research, firehose news) lands in the same candidate pool,
the same story arriving through several channels is published once, and a story
whose timestamp will not normalise never occupies a rerank slot.
"""

from __future__ import annotations

import pytest

from rugbrain import content_candidates, content_story, content_time


def _hit(
    source: str,
    content: str,
    *,
    ts: str = "",
    category: str = "",
    tweet_id: str = "",
    **extra: object,
) -> dict[str, object]:
    """One Qdrant-shaped search result."""
    md: dict[str, object] = {"source": source, "ts": ts, "category": category}
    if tweet_id:
        md["tweet_id"] = tweet_id
    md.update(extra)
    return {"id": f"{source}-{content[:12]}", "content": content, "source": source, "metadata": md}


class FakeStore:
    """Returns canned hits per (collection, category-filter) and records calls."""

    def __init__(self, by_key: dict[tuple[str, str | None], list[dict[str, object]]]) -> None:
        self.by_key = by_key
        self.calls: list[tuple[str, str | None]] = []

    async def search(
        self,
        query_embedding: list[float],
        collection: str | None = None,
        limit: int = 10,
        min_similarity: float = 0.0,
        filters: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        category = (filters or {}).get("metadata.category")
        self.calls.append((str(collection), category))
        return list(self.by_key.get((str(collection), category), []))


async def _embed(text: str) -> list[float]:
    return [0.0] * 8


async def _rerank(stories: list[dict[str, object]], keep: int = 8) -> list[dict[str, object]]:
    return stories[:keep]


async def _select(store: FakeStore) -> list[dict[str, object]]:
    return await content_candidates.recent_stories(
        embed=_embed, store_fn=lambda: _as_awaitable(store), rerank=_rerank
    )


async def _as_awaitable(store: FakeStore) -> FakeStore:
    return store


def test_every_source_family_is_declared() -> None:
    """One collection per source family; undated corpora stay out."""
    assert content_candidates.CANDIDATE_COLLECTIONS == (
        "news_v2",
        "social_intel",
        "research_v2",
        "news_articles",
    )
    for excluded in ("threat_intel_v2", "scam_intel_v2", "rag_documents"):
        assert excluded not in content_candidates.CANDIDATE_COLLECTIONS


@pytest.mark.asyncio
async def test_telegram_x_and_research_all_reach_one_pool() -> None:
    """Each source family contributes a candidate to the SAME selection."""
    store = FakeStore(
        {
            ("news_v2", None): [
                _hit(
                    "telegram:Noahh", "Exchange halted withdrawals", ts="2026-10-20T10:00:00+00:00"
                )
            ],
            ("news_v2", "x_social"): [
                _hit(
                    "x:zksync",
                    "Bridge exploit drained 4M",
                    ts="Wed Sep 16 18:18:29 +0000 2026",
                    tweet_id="123",
                )
            ],
            ("social_intel", None): [
                _hit(
                    "telegram:CoinGlass", "Funding flipped negative", ts="2026-10-16T21:36:18+00:00"
                )
            ],
            ("research_v2", None): [
                _hit(
                    "trailofbits_blog_rss",
                    "Auditing in the age of AI",
                    published="2026-09-18T07:00:00-04:00",
                )
            ],
        }
    )

    stories = await _select(store)

    assert {s["collection"] for s in stories} == {"news_v2", "social_intel", "research_v2"}
    assert "x:zksync" in {s["source"] for s in stories}
    probed = {collection for collection, _ in store.calls}
    assert set(content_candidates.CANDIDATE_COLLECTIONS) <= probed


@pytest.mark.asyncio
async def test_x_lane_filter_is_scoped_to_news_v2() -> None:
    """The x_social partition is probed once, on the collection that holds it."""
    store = FakeStore({})
    await _select(store)

    filtered = [call for call in store.calls if call[1] is not None]
    assert filtered == [("news_v2", content_candidates.X_CATEGORY)]


@pytest.mark.asyncio
async def test_same_story_through_three_channels_publishes_once() -> None:
    """Syndication across Telegram, X and RSS is one story, with corroboration."""
    story = "SEC hack drains 12M from treasury"
    store = FakeStore(
        {
            ("news_v2", None): [
                _hit(
                    "telegram:alerts",
                    f"🚨 {story} https://t.me/alerts/1",
                    ts="2026-10-20T10:00:00+00:00",
                )
            ],
            ("news_v2", "x_social"): [
                _hit(
                    "x:watcherguru",
                    f"@watcherguru {story} https://x.com/i/status/9",
                    ts="Mon Oct 20 10:05:00 +0000 2026",
                    tweet_id="9",
                )
            ],
            ("research_v2", None): [
                _hit(
                    "cointelegraph_rss",
                    f"{story} https://cointelegraph.com/a",
                    published="2026-10-20T10:10:00+00:00",
                )
            ],
        }
    )

    stories = await _select(store)

    assert len(stories) == 1
    kept = stories[0]
    assert kept["corroboration"] == 3
    assert len(kept["also_seen_in"]) == 2


@pytest.mark.asyncio
async def test_different_stories_are_not_collapsed() -> None:
    """The collapse must not merge two genuinely different stories."""
    store = FakeStore(
        {
            ("news_v2", None): [
                _hit(
                    "telegram:a",
                    "SEC hack drains 12M from treasury",
                    ts="2026-10-20T10:00:00+00:00",
                ),
                _hit(
                    "telegram:b",
                    "Solana validator client patch released",
                    ts="2026-10-20T11:00:00+00:00",
                ),
            ]
        }
    )

    assert len(await _select(store)) == 2


@pytest.mark.asyncio
async def test_undated_story_never_takes_a_rerank_slot() -> None:
    """A story with no normalisable timestamp is dropped before rerank."""
    store = FakeStore(
        {
            ("news_articles", None): [
                _hit("theblock", "Story with only a story_id", story_id="0eaa8cb3")
            ],
            ("news_v2", None): [_hit("telegram:a", "Dated story", ts="2026-10-20T10:00:00+00:00")],
        }
    )

    stories = await _select(store)

    assert [s["source"] for s in stories] == ["telegram:a"]


@pytest.mark.asyncio
async def test_empty_pool_records_the_reason() -> None:
    """A pool of nothing but undated hits says so instead of looking quiet."""
    store = FakeStore({("news_articles", None): [_hit("theblock", "no timestamp", story_id="x")]})

    assert await _select(store) == []
    assert content_candidates.LAST_REASON.startswith("empty_pool:")


def test_research_payload_maps_published_and_source_url() -> None:
    """research_v2 carries ``published``/``source_url``, not ``ts``/``url``."""
    story = content_story.normalise(
        {
            "id": "r1",
            "content": "Far From Random: Dart PRNG.\nSummary\nPublished: 2026-09-18T07:00:00-04:00",
            "source": "zellic_blog_rss",
            "metadata": {
                "published": "2026-09-18T07:00:00-04:00",
                "source_url": "https://zellic.io/x",
            },
        },
        "research_v2",
    )

    assert story["ts"] == "2026-09-18T07:00:00-04:00"
    assert story["url"] == "https://zellic.io/x"
    assert story["title"].startswith("Far From Random")


def test_tweet_dialect_is_normalised_to_iso() -> None:
    """The X lane's raw tweet timestamp must become ISO-8601."""
    assert content_time.to_iso8601("Wed Sep 16 18:18:29 +0000 2026") == "2026-09-16T18:18:29+00:00"
    assert content_time.to_iso8601("Fri, 18 Sep 2026 07:00:00 +0000") == "2026-09-18T07:00:00+00:00"
    assert content_time.to_iso8601("not a date") == ""
    assert content_time.to_iso8601("") == ""
