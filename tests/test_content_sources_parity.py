"""Parity with the news channel, and cross-source dedup proven end to end.

The claim under test is narrow and checkable: an item from Medium, paragraph or
YouTube is shaped exactly like a ``firehose.direct_sources`` news article, and
the same story arriving from two publishers collapses to ONE canonical item that
still cites both. No network, no Qdrant — the pipeline is injected.
"""

from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
from typing import Any

import pytest

from rugbrain.ingest_sources.content_sources import (
    SourceSpec,
    collapse_items,
    ingest_items,
    to_news_item,
)
from rugbrain.ingest_sources.content_sources.parity import NEWS_COLLECTION

NEWS = SourceSpec(
    id="news-alpha",
    kind="news",
    publisher="Alpha Wire",
    feed_url="https://alpha.example/feed",
    licence="rss-syndication",
    fetched_at="2026-10-25T00:00:00Z",
    homepage="https://alpha.example",
    category="crypto",
)
MEDIUM = SourceSpec(
    id="medium-beta",
    kind="medium",
    publisher="Beta Publication",
    feed_url="https://medium.com/feed/beta",
    licence="medium-public-feed",
    fetched_at="2026-10-25T00:00:00Z",
)

STORY = (
    "The SEC approved a spot solana ETF after a two year review, clearing the "
    "way for issuers to list the product next quarter."
)
ENTRY_A = {
    "title": "SEC approves spot Solana ETF",
    "summary": STORY,
    "url": "https://alpha.example/solana-etf",
    "published_at": "Fri, 24 Oct 2026 13:00:00 GMT",
}
ENTRY_B = {
    "title": "Spot Solana ETF wins SEC approval",
    "summary": STORY,
    "url": "https://medium.com/beta/solana-etf",
    "published_at": "2026-10-24T14:30:00+00:00",
}


def test_item_matches_the_news_article_contract() -> None:
    item = to_news_item(NEWS, ENTRY_A)
    metadata = item["metadata"]
    assert item["collection"] == NEWS_COLLECTION == "news_articles"
    assert item["content"] == f"News: {ENTRY_A['title']}. {STORY}"[:2000]
    for key in ("source", "url", "published", "category", "title"):
        assert key in metadata
    assert metadata["source"] == "news-alpha"
    assert isinstance(metadata["tickers"], list)
    assert isinstance(metadata["entities"], dict)


def test_item_carries_publisher_licence_and_sentiment() -> None:
    metadata = to_news_item(NEWS, ENTRY_A)["metadata"]
    assert metadata["provenance"]["licence"] == "rss-syndication"
    assert metadata["provenance"]["publisher"] == "Alpha Wire"
    assert metadata["sentiment"]["label"] in {"neutral", "bullish", "bearish"}


@pytest.mark.parametrize("entry", [ENTRY_A, ENTRY_B])
def test_published_is_rfc2822_whatever_the_source_used(entry: dict[str, str]) -> None:
    published = to_news_item(MEDIUM, entry)["metadata"]["published"]
    assert parsedate_to_datetime(published).tzinfo is not None


def test_cashtag_tickers_travel_into_item_metadata() -> None:
    item = to_news_item(NEWS, {**ENTRY_A, "summary": "A $SOL ETF was approved today."})
    assert item["metadata"]["tickers"] == ["SOL"]


def test_youtube_item_keeps_timestamped_transcript_chunks() -> None:
    spec = SourceSpec(
        id="yt-gamma",
        kind="youtube",
        publisher="Gamma",
        feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCTEST",
        licence="youtube-standard",
        fetched_at="2026-10-25T00:00:00Z",
        channel_id="UCTEST",
    )
    chunks = [
        {"start": 0.0, "end": 60.0, "start_ts": "0:00:00", "end_ts": "0:01:00", "text": STORY}
    ]
    metadata = to_news_item(
        spec,
        {
            "title": "ETF explainer",
            "url": "https://youtu.be/x",
            "published_at": "2026-10-24T10:00:00Z",
        },
        transcript_chunks=chunks,
    )["metadata"]
    assert metadata["transcript_chunks"] == chunks
    assert metadata["transcript_status"] == "captions:unknown"
    assert metadata["kind"] == "youtube"


def test_same_story_from_two_sources_collapses_to_one_with_both_citations() -> None:
    items = [to_news_item(NEWS, ENTRY_A), to_news_item(MEDIUM, ENTRY_B)]
    canonical = collapse_items(items)
    assert len(canonical) == 1
    citations = canonical[0]["metadata"]["citations"]
    assert {citation["source"] for citation in citations} == {"news-alpha", "medium-beta"}
    assert {citation["url"] for citation in citations} == {
        ENTRY_A["url"],
        ENTRY_B["url"],
    }
    assert canonical[0]["metadata"]["corroboration"] == 2


def test_different_stories_are_not_collapsed() -> None:
    other = {
        "title": "Tether attestation shows $4B surplus",
        "summary": "Tether published its quarterly attestation with a four billion dollar buffer.",
        "url": "https://alpha.example/tether",
        "published_at": "Fri, 24 Oct 2026 15:00:00 GMT",
    }
    assert len(collapse_items([to_news_item(NEWS, ENTRY_A), to_news_item(NEWS, other)])) == 2


class _FakePipeline:
    """Records what the news pipeline would have been asked to store."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def ingest(
        self, collection: str, content: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((collection, content, metadata))
        return {"status": "ingested", "id": f"id-{len(self.calls)}"}


def test_ingest_delegates_once_per_canonical_item() -> None:
    pipeline = _FakePipeline()
    items = [to_news_item(NEWS, ENTRY_A), to_news_item(MEDIUM, ENTRY_B)]
    report = asyncio.run(ingest_items(items, pipeline=pipeline))
    assert report["received"] == 2
    assert report["collapsed"] == 1
    assert report["ingested"] == 1
    assert report["ids"] == ["id-1"]
    collection, _, metadata = pipeline.calls[0]
    assert collection == NEWS_COLLECTION
    assert len(metadata["citations"]) == 2


def test_collapse_uses_the_shared_dedup_threshold() -> None:
    from rugbrain.ingest_sources.ingest_dedupe import DUP_THRESHOLD

    assert pytest.approx(0.62) == DUP_THRESHOLD
    near = {**ENTRY_A, "summary": STORY + " Issuers expect a launch next quarter."}
    assert len(collapse_items([to_news_item(NEWS, ENTRY_A), to_news_item(NEWS, near)])) == 1
