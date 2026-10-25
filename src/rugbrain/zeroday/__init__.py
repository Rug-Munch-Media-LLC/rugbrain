"""0-day / exploit / bounty intelligence pipeline.

Fetch public feeds (advisories, exploit trackers, hack write-ups, bounty
boards), normalize them into one :class:`~rugbrain.zeroday.schema.Record`
shape, dedupe across sources, emit a change feed with first-source latency,
and alert on critical items. Public sources only, no auth bypass.
"""

from rugbrain.zeroday.pipeline import Change, IngestResult, merge
from rugbrain.zeroday.registry import SourceSpec, fetch_all, fetch_source
from rugbrain.zeroday.schema import KINDS, Record

__all__ = [
    "KINDS",
    "Change",
    "IngestResult",
    "Record",
    "SourceSpec",
    "fetch_all",
    "fetch_source",
    "merge",
]
