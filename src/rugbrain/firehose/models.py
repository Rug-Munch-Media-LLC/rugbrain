"""Firehose data structures — feed source and statistics containers."""

from dataclasses import dataclass, field
from typing import Any

__all__ = ["FeedSource", "FirehoseStats"]


@dataclass
class FeedSource:
    """A data source the firehose pulls from."""

    name: str
    collection: str
    cadence_seconds: int
    fetch_fn: Any = field(repr=False)
    enabled: bool = True
    description: str = ""
    last_run: float = 0
    runs: int = 0
    docs_ingested: int = 0
    docs_skipped: int = 0
    errors: int = 0


@dataclass
class FirehoseStats:
    """Current firehose statistics."""

    running: bool = False
    uptime_seconds: float = 0
    total_sources: int = 0
    total_ingested: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_activity: float = 0
