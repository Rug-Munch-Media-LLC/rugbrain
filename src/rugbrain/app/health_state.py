"""The mutable process health dict — the single source of truth.

One job: own one dict and provide the only three operations the rest of the
app performs on it (read a key, set a key, take a copy). No I/O, no imports
beyond stdlib typing, so it is a leaf every other ``app`` module can depend on
without creating a cycle.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Initial shape. Keys are additive at runtime; consumers must use ``.get``.
STATE: dict[str, Any] = {
    "status": "starting",
    "qdrant": False,
    "qdrant_schema": False,
    "redis": False,
    "qdrant_url": "",
    "redis_host": "",
    "scanner_subscriber": False,
    "subscriber_stats": {},
    "scanner_poller": False,
    "poller_stats": {},
    "mempool_scanner": False,
    "social_sentiment": False,
    "realtime_monitor": False,
    "realtime_monitor_stats": {},
    "news_story_updater": False,
    "news_story_updater_stats": {},
}


def set_flag(name: str, value: Any) -> None:
    """Record one subsystem's status. Unknown names are allowed (additive)."""
    STATE[name] = value


def snapshot() -> dict[str, Any]:
    """Return a shallow copy so callers cannot mutate the live dict."""
    return dict(STATE)
