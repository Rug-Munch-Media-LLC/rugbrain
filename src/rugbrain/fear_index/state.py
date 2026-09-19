"""Shared mutable state for the fear-index package.

All state lives here and is imported into the package namespace
(``rugbrain.fear_index``); sibling modules read/write it through the package
at call time so monkeypatching a package attribute stays effective.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from .storage import FearIndexStorage

# In-memory history ring and last snapshot.
_history: deque[dict[str, Any]] = deque(maxlen=10_000)
_last_snapshot: dict[str, Any] | None = None

# Best-effort ClickHouse persistence ledger.
_storage = FearIndexStorage()

# Background poller task + lock.
_poller_task: asyncio.Task[Any] | None = None
_poller_lock = asyncio.Lock()
