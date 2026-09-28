"""Programmatic top-tier crypto data sources for RugBrain ingestion.

This module is the stable public surface. The catalogue lives in
:mod:`rugbrain.ingest_sources.registry`, fetching in ``fetch``, the failure
types in ``outcome``, the CLI in ``cli``, and the per-source licence gate in
``licenses`` — each a single job.

Every name below is imported *into* this module's namespace, not defined here,
so ``from rugbrain.ingest_sources import X`` keeps working for every existing
caller and ``monkeypatch.setattr(rugbrain.ingest_sources, name, ...)`` keeps
patching the binding those callers resolve.

Usage::

    python -m rugbrain.ingest_sources --source swc_registry --limit 3
    python -m rugbrain.ingest_sources --list
"""

from __future__ import annotations

from rugbrain.ingest_sources.base import IngestDoc, Source, to_crawl_document
from rugbrain.ingest_sources.fetch import (
    fetch_all,
    fetch_all_results,
    fetch_source,
    fetch_source_result,
)
from rugbrain.ingest_sources.outcome import FetchFailure, SourceResult
from rugbrain.ingest_sources.registry import DOC_TYPES, SOURCES, get_source, list_sources

__all__ = [
    "DOC_TYPES",
    "SOURCES",
    "FetchFailure",
    "IngestDoc",
    "Source",
    "SourceResult",
    "fetch_all",
    "fetch_all_results",
    "fetch_source",
    "fetch_source_result",
    "get_source",
    "list_sources",
    "to_crawl_document",
]
