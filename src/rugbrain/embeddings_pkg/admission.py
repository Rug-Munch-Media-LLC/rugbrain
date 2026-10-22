"""Admission control for the shared 8B embedding lane.

llama-server serves a small fixed number of slots (``--parallel 2`` on the
local 4096d lane). Ingestion workers fanned out one embed per document with no
bound, so the deferred queue stayed ~6 deep and a SEARCH query timed out before
its own request ever reached a slot — surfacing as the hard
``RAGUnavailableError`` ("chain exhausted") even though the lane was healthy.

The gate caps in-flight embed HTTP calls at ``RAG_EMBED_MAX_INFLIGHT``
(default 2, matching the slot count) so a query waits a short turn instead of
racing a retry storm. Process-local by design: one rugbrain process can never
occupy more than its share of the lane.
"""

from __future__ import annotations

import asyncio
import os

#: Rung slots on the local llama.cpp lane (``--parallel 2``).
DEFAULT_SLOTS: int = 2

_sem: asyncio.Semaphore | None = None
_sem_loop: asyncio.AbstractEventLoop | None = None


def gate() -> asyncio.Semaphore:
    """The process-wide embed gate, rebuilt when the event loop changes.

    Tests and short-lived scripts create a fresh loop per call, so the
    semaphore is re-created whenever the running loop differs from the one it
    was built on; a stale semaphore would raise "bound to a different loop".
    """
    global _sem, _sem_loop
    loop = asyncio.get_running_loop()
    if _sem is None or _sem_loop is not loop:
        slots = int(os.getenv("RAG_EMBED_MAX_INFLIGHT", str(DEFAULT_SLOTS)) or DEFAULT_SLOTS)
        _sem = asyncio.Semaphore(max(1, slots))
        _sem_loop = loop
    return _sem
