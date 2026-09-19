"""
RAG Firehose - Continuous Intelligence Ingestion Engine
=======================================================

Self-feeding RAG pipeline that continuously pulls, filters, and ingests
crypto intelligence from multiple sources at different cadences.

This package initializer is a thin re-export facade; the implementation
lives in sibling modules:

  - ``config``         — collection caps, quality floor, rate limits
  - ``models``         — FeedSource / FirehoseStats dataclasses
  - ``quality``        — scoring + entity extraction (pure functions)
  - ``pipeline``       — IngestionPipeline (dedup → quality → store)
  - ``chain_sources``  — etherscan labels, solana scams, pattern extraction
  - ``sources``        — FeedSources facade + default source registry
  - ``engine``         — FirehoseEngine + singleton accessor
"""

from .config import COLLECTION_CAPS, MIN_QUALITY_SCORE, RATE_LIMITS
from .engine import FirehoseEngine, get_firehose
from .models import FeedSource, FirehoseStats
from .pipeline import IngestionPipeline
from .sources import FeedSources

__all__ = [
    "COLLECTION_CAPS",
    "MIN_QUALITY_SCORE",
    "RATE_LIMITS",
    "FeedSource",
    "FeedSources",
    "FirehoseEngine",
    "FirehoseStats",
    "IngestionPipeline",
    "get_firehose",
]
