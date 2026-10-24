"""Crypto news, Substack, Medium, paragraph.xyz and YouTube as one pipeline.

Every added publisher is described once, with provenance, in
``config/content_sources.json``. Acquisition is one bounded conditional GET per
source; dedup/embed/rerank are the news channel's own functions, not copies.
"""

from __future__ import annotations

from rugbrain.ingest_sources.content_sources.catalog import load_catalog
from rugbrain.ingest_sources.content_sources.collapse import collapse_items
from rugbrain.ingest_sources.content_sources.fetch import fetch_source
from rugbrain.ingest_sources.content_sources.parity import to_news_item
from rugbrain.ingest_sources.content_sources.pipeline import ingest_items
from rugbrain.ingest_sources.content_sources.rank import rank_items
from rugbrain.ingest_sources.content_sources.sentiment import analyze_item
from rugbrain.ingest_sources.content_sources.spec import SourceSpec, parse_specs
from rugbrain.ingest_sources.content_sources.transcript import chunk_transcript
from rugbrain.ingest_sources.content_sources.youtube_feed import parse_channel_feed
from rugbrain.ingest_sources.content_sources.youtube_transcript import transcript_plan

__all__ = [
    "SourceSpec",
    "analyze_item",
    "chunk_transcript",
    "collapse_items",
    "fetch_source",
    "ingest_items",
    "load_catalog",
    "parse_channel_feed",
    "parse_specs",
    "rank_items",
    "to_news_item",
    "transcript_plan",
]
