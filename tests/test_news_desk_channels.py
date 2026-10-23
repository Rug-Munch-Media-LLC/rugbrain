"""Tests for the curated news-desk channel layer + its dedupe contract.

The news-desk list is DATA, but wrong data is expensive in a way a wrong
function is not: a mistyped handle silently sweeps a lookalike channel into
the news collection forever, and a duplicate handle pays twice for the same
fetch. These tests pin the invariants that make the list trustworthy, and pin
the near-duplicate behaviour that stops one wire story becoming five rows.
"""

from __future__ import annotations

import importlib as _il
import re

deck = _il.import_module("rugbrain.ingest_sources.news_desk_channels")
channels = _il.import_module("rugbrain.ingest_sources.alt_ingest_channels")
dedupe = _il.import_module("rugbrain.ingest_sources.ingest_dedupe")

HANDLE_RE = re.compile(r"^[a-z][a-z0-9_]{3,31}$")


def test_every_row_is_a_public_handle_with_a_publisher() -> None:
    """Handle shape + a named publisher: the provenance that it is a newsroom."""
    assert deck.NEWS_DESK_CHANNELS, "news desk list must not be empty"
    for handle, publisher, seen in deck.NEWS_DESK_CHANNELS:
        assert HANDLE_RE.match(handle), f"bad handle shape: {handle!r}"
        assert publisher.strip(), f"missing publisher provenance for {handle}"
        assert seen >= 1, f"no message count recorded for {handle}"


def test_handles_are_unique_and_lowercased() -> None:
    """One row per outlet: a duplicate pays twice for the same fetch."""
    handles = [h for h, _, _ in deck.NEWS_DESK_CHANNELS]
    assert len(handles) == len(set(handles)), "duplicate desk handle"
    assert handles == [h.lower() for h in handles], "handles must be lowercase"


def test_resolve_news_desk_strips_at_and_dedupes_in_order() -> None:
    """A pasted ``@handle`` must resolve to the string the URL builder wants."""
    assert deck.resolve_news_desk() == [h.lower() for h, _, _ in deck.NEWS_DESK_CHANNELS]
    assert deck.resolve_news_desk(["@THEBLOCK", "theblock", " jinse ", ""]) == [
        "theblock",
        "jinse",
    ]


def test_alt_ingest_includes_every_desk_handle_exactly_once() -> None:
    """The registry the sweeps consume must carry the desks, and not twice."""
    lowered = [c.lower() for c in channels.ALT_INGEST_CHANNELS]
    for handle in deck.NEWS_DESK_HANDLES:
        assert lowered.count(handle) == 1, f"{handle} covered {lowered.count(handle)}x"


def test_resolve_channel_list_defaults_to_the_full_registry() -> None:
    """An empty override means 'all', never 'none' — a zero-channel sweep is a lie."""
    assert channels.resolve_channel_list(None) == list(channels.ALT_INGEST_CHANNELS)
    assert channels.resolve_channel_list([]) == list(channels.ALT_INGEST_CHANNELS)
    assert channels.resolve_channel_list(["@TheBlock"]) == ["TheBlock"]


def test_syndicated_story_normalizes_to_one_identity() -> None:
    """The same wire story with different links/emoji is ONE story per dedupe."""
    wire = "SEC approves spot ETH ETF, effective next quarter"
    a = f"🚨 {wire} https://theblock.example/a"
    b = f"{wire} @decryptnews https://decrypt.example/b"
    assert dedupe.normalize_for_dedupe(a) == dedupe.normalize_for_dedupe(b)
    assert (
        dedupe.jaccard(
            dedupe.shingles(dedupe.normalize_for_dedupe(a)),
            dedupe.shingles(dedupe.normalize_for_dedupe(b)),
        )
        == 1.0
    )


def test_distinct_stories_are_not_collapsed() -> None:
    """Vocabulary overlap alone must not merge two different desks' stories."""
    a = "SEC approves spot ETH ETF for listing next quarter"
    b = "SEC charges exchange over unregistered ETH staking product"
    score = dedupe.jaccard(
        dedupe.shingles(dedupe.normalize_for_dedupe(a)),
        dedupe.shingles(dedupe.normalize_for_dedupe(b)),
    )
    assert score < dedupe.DUP_THRESHOLD
