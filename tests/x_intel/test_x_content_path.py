"""X content path: attribution, thread/status normalization, lane provenance.

These pin the three defects measured 2026-10-24 on the live corpus: retweets
published under the retweeter's handle, the same post stored twice (once with no
author), and every embedded X post invisible to content selection because the
unified read filtered on the base collection name while the payload held the
lane twin.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugbrain.ingest_sources.x_intel.dedupe import text_fingerprint, tweet_to_record
from rugbrain.ingest_sources.x_intel.normalize import normalize_tweet
from rugbrain.ingest_sources.x_intel.normalize_thread import (
    full_text,
    looks_truncated,
    normalize_status,
    retweet_origin,
    thread_fields,
)
from rugbrain.ingest_sources.x_intel.pipeline_report import finalize
from rugbrain.ingest_sources.x_intel.storage import store_reason
from rugbrain.qdrant_store.namespace import provenance_condition


def test_retweet_is_attributed_to_the_original_author() -> None:
    wrapper = {
        "id": "900",
        "text": "RT @PeckShieldAlert: summerfinance drained for $6M",
        "author": {"username": "peckshield"},
    }
    norm = normalize_tweet(wrapper)
    assert norm is not None
    assert norm["is_retweet"] is True
    assert norm["handle"] == "PeckShieldAlert"
    assert norm["via_handle"] == "peckshield"
    assert norm["text"] == "summerfinance drained for $6M"
    record = tweet_to_record(norm)
    assert record is not None
    assert "retweeted by @peckshield" in record["content"]
    assert record["metadata"]["kind"] == "x_retweet"


def test_nested_retweet_uses_the_origin_status_identity() -> None:
    norm = normalize_tweet(
        {
            "id": "1",
            "author": {"username": "wrapper"},
            "retweeted_status": {
                "id": "2",
                "text": "original words",
                "author": {"username": "orig"},
            },
        }
    )
    assert norm is not None
    assert norm["tweet_id"] == "2" and norm["handle"] == "orig"
    assert norm["url"] == "https://x.com/orig/status/2"


def test_long_post_keeps_note_tweet_body_not_the_preview() -> None:
    body = "exploit detail " * 60
    payload = {"id": "5", "text": "exploit detail…", "note_tweet": {"text": body}}
    assert full_text(payload) == body.strip()
    norm = normalize_tweet(payload)
    assert norm is not None and len(norm["text"]) > 500
    assert looks_truncated("a post that ends in …") is True
    assert looks_truncated("complete post") is False


def test_thread_and_quote_linkage_are_carried() -> None:
    fields = thread_fields(
        {
            "id": "3",
            "in_reply_to_status_id": "2",
            "in_reply_to_username": "root",
            "conversation_id": "1",
            "quoted": {"id": "9", "author": {"username": "cited"}},
        }
    )
    assert fields == {
        "conversation_id": "1",
        "in_reply_to": "2",
        "in_reply_to_handle": "root",
        "is_reply": True,
        "quoted_id": "9",
        "quoted_handle": "cited",
    }


def test_status_payload_splits_parent_from_replies() -> None:
    status = normalize_status(
        {"tweet": {"id": "10", "text": "root post"}, "replies": [{"id": "11", "text": "reply"}]}
    )
    assert status is not None
    assert status["tweet"]["id"] == "10" and len(status["replies"]) == 1
    assert status["thread_id"] == "10"
    assert normalize_status("not-a-dict") is None


def test_unknown_handle_never_renders_a_fake_author() -> None:
    record = tweet_to_record(
        {"tweet_id": "7", "text": "exploit drained 5M", "url": "https://x.com/i/status/7"}
    )
    assert record is not None
    assert record["content"] == "[X] exploit drained 5M"
    assert "@" not in record["content"].split(":")[0]
    assert record["source"] == "x.com"


def test_identical_bodies_dedupe_across_ids() -> None:
    """The same post fetched with and without an author must not land twice."""
    assert text_fingerprint(" A  post ") == text_fingerprint("a post")


@pytest.mark.asyncio
async def test_dedupe_collapses_duplicate_body_in_batch() -> None:
    from rugbrain.ingest_sources.x_intel.dedupe import dedupe_seen

    tweets = [
        {"tweet_id": "1", "text": "same words"},
        {"tweet_id": "2", "text": "same   words"},
        {"tweet_id": "3", "text": "different"},
    ]
    fresh = await dedupe_seen(tweets)
    assert [t["tweet_id"] for t in fresh] == ["1", "3"]


def test_provenance_condition_accepts_base_and_lane_twin() -> None:
    cond = provenance_condition("news_v2")
    values = [c["match"]["value"] for c in cond["should"]]
    assert "news_v2" in values and "news_v2_8b" in values


def test_store_reason_names_the_failure() -> None:
    assert store_reason({"inserted": 3, "failed": 0, "skipped": 0}) == ""
    assert store_reason({"inserted": 0, "failed": 4, "skipped": 0}) == "embed_or_upsert_failed"
    assert store_reason({"inserted": 0, "failed": 0, "skipped": 2}) == "already_present"
    assert store_reason({"inserted": 0, "failed": 0, "skipped": 0}) == "no_chunks"


def test_finalize_reports_why_nothing_was_ingested() -> None:
    summary = {
        "fetched": 5,
        "unique": 2,
        "ingested": 0,
        "reason": "",
        "store_reason": "embed_or_upsert_failed",
    }
    out = finalize(dict(summary), pry_lane_dead=False)
    assert out["reason"] == "embed_or_upsert_failed"
    assert finalize({"fetched": 3, "unique": 0, "ingested": 0, "reason": ""}, False)["reason"] == (
        "all_duplicate_or_dust"
    )


class _FakeStore:
    """Records the filters each probe used; returns X rows only for the X filter."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def search(self, embedding, collection, limit, min_similarity, filters=None):
        self.calls.append({"collection": collection, "filters": filters, "limit": limit})
        if filters:
            return [
                {
                    "id": "x1",
                    "similarity": 0.4,
                    "content": "exploit drained",
                    "source": "x.com/peckshield",
                    "metadata": {"category": "x_social", "tweet_id": "1", "url": "u"},
                }
            ]
        return [
            {
                "id": "n1",
                "similarity": 0.5,
                "content": "news story",
                "source": "site",
                "metadata": {"category": "news", "url": "n"},
            }
        ]


async def _stub_embed(_text: str) -> list[float]:
    return [0.0] * 4


async def _dead_embed(_text: str) -> list[float]:
    raise RuntimeError("no embedding backend available")


@pytest.mark.asyncio
async def test_selection_always_probes_the_x_lane() -> None:
    """The X lane is probed with its own filter, so an X post is a candidate."""
    from rugbrain import content_candidates

    store = _FakeStore()
    stories = await content_candidates.recent_stories(
        limit=5, embed=_stub_embed, store_fn=_store_fn(store), rerank=_identity_rerank
    )
    assert {s["category"] for s in stories} == {"news", "x_social"}
    x_calls = [c for c in store.calls if c["filters"]]
    assert x_calls and x_calls[0]["filters"] == {"metadata.category": "x_social"}


async def _identity_rerank(stories, **_kwargs):
    """Stand-in for the cross-encoder: keep the retrieval order."""
    return list(stories)


def _store_fn(store: _FakeStore):
    async def _get_store() -> _FakeStore:
        return store

    return _get_store


@pytest.mark.asyncio
async def test_selection_fails_open_with_a_reason() -> None:
    from rugbrain import content_candidates

    assert (
        await content_candidates.recent_stories(
            limit=5, embed=_dead_embed, store_fn=_store_fn(_FakeStore())
        )
        == []
    )
    assert content_candidates.LAST_REASON.startswith("embed_unavailable:")


def test_retweet_origin_absent_for_plain_post() -> None:
    assert retweet_origin({"id": "1", "text": "plain"}) is None
