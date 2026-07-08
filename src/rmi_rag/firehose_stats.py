"""Firehose statistics tracking."""
from __future__ import annotations

from dataclasses import field


class FirehoseStats:
    """Current firehose statistics."""

    running: bool = False
    uptime_seconds: float = 0
    total_sources: int = 0
    total_ingested: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    sources: dict[str, dict] = field(default_factory=dict)
    last_activity: float = 0


# ──────────────────────────────────────────────────────────────
# Smart Ingestion Pipeline
# ──────────────────────────────────────────────────────────────


