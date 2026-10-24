"""YouTube: channel feed parsing, transcript fallback order, timestamped chunks.

Fixtures only — the XML is a trimmed copy of a real ``feeds/videos.xml``
response, so no test touches YouTube.
"""

from __future__ import annotations

from rugbrain.ingest_sources.content_sources import (
    SourceSpec,
    chunk_transcript,
    parse_channel_feed,
    transcript_plan,
)

SPEC = SourceSpec(
    id="yt-test",
    kind="youtube",
    publisher="Test Channel",
    feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCTEST",
    licence="youtube-standard",
    fetched_at="2026-10-25T00:00:00Z",
    channel_id="UCTEST",
)

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/">
 <entry>
  <id>yt:video:abc123</id>
  <yt:videoId>abc123</yt:videoId>
  <title>Why the merge broke L2 fees</title>
  <published>2026-10-24T13:00:07+00:00</published>
  <author><name>Test Channel</name></author>
  <media:group>
   <media:description>A 12 minute explainer on blob pricing.</media:description>
  </media:group>
 </entry>
 <entry>
  <yt:videoId>def456</yt:videoId>
  <title>Solana validator economics</title>
  <published>2026-10-23T09:30:00+00:00</published>
 </entry>
</feed>
"""


def test_channel_feed_entries_carry_id_title_channel_and_published() -> None:
    entries = parse_channel_feed(FEED, SPEC)
    assert [entry["video_id"] for entry in entries] == ["abc123", "def456"]
    first = entries[0]
    assert first["title"] == "Why the merge broke L2 fees"
    assert first["channel"] == "Test Channel"
    assert first["published_at"].startswith("2026-10-24T13:00:07")
    assert first["url"] == "https://www.youtube.com/watch?v=abc123"
    assert first["summary"] == "A 12 minute explainer on blob pricing."


def test_second_entry_falls_back_to_the_spec_publisher() -> None:
    entries = parse_channel_feed(FEED, SPEC)
    assert entries[1]["channel"] == "Test Channel"
    assert entries[1]["summary"] == ""


def test_malformed_feed_is_data_not_an_exception() -> None:
    assert parse_channel_feed("", SPEC) == []
    assert parse_channel_feed("<html>blocked</html>", SPEC) == []


def test_transcript_plan_prefers_yt_dlp_then_api_then_description() -> None:
    assert transcript_plan(yt_dlp_available=True, transcript_api_available=True) == (
        "captions:yt-dlp",
        "captions:youtube-transcript-api",
        "metadata:description-only",
    )
    assert transcript_plan(yt_dlp_available=False, transcript_api_available=True)[0] == (
        "captions:youtube-transcript-api"
    )
    assert transcript_plan(yt_dlp_available=False, transcript_api_available=False) == (
        "metadata:description-only",
    )


def test_transcript_chunks_split_at_the_window_boundary() -> None:
    cues = [
        {"start": 0.0, "end": 5.0, "text": "Welcome back."},
        {"start": 5.0, "end": 40.0, "text": "Today we look at blob fees."},
        {"start": 40.0, "end": 95.0, "text": "They spiked after the merge."},
        {"start": 95.0, "end": 140.0, "text": "Here is the chart."},
        {"start": 140.0, "end": 150.0, "text": "Thanks for watching."},
    ]
    chunks = chunk_transcript(cues, window_s=120.0)
    assert len(chunks) == 2
    assert chunks[0]["start"] == 0.0 and chunks[0]["end"] == 95.0
    # The cue that crosses the two-minute window OPENs the next chunk, so no
    # timestamp is lost between windows.
    assert chunks[1]["start"] == 95.0 and chunks[1]["end"] == 150.0
    assert "blob fees" in chunks[0]["text"]


def test_transcript_chunks_expose_human_timestamps() -> None:
    cues = [
        {"start": 0.0, "end": 95.0, "text": "first"},
        {"start": 95.0, "end": 150.0, "text": "second"},
    ]
    chunks = chunk_transcript(cues, window_s=60.0)
    assert [chunk["start_ts"] for chunk in chunks] == ["0:00:00", "0:01:35"]
    assert [chunk["end_ts"] for chunk in chunks] == ["0:01:35", "0:02:30"]


def test_empty_cues_are_skipped_and_character_cap_splits() -> None:
    cues = [
        {"start": 0, "end": 1, "text": ""},
        {"start": 1, "end": 2, "text": "a" * 30},
        {"start": 2, "end": 3, "text": "b" * 30},
    ]
    assert len(chunk_transcript(cues, max_chars=40)) == 2
