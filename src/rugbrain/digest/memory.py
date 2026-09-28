"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
Persistent agent-memory seam, mem0-style — public surface.

This module is a **facade**. The responsibilities behind it each live in
one module with one job:

  * ``memory_types``   — what a Memory is; the MemoryStore port
  * ``memory_filter``  — which candidate text is durable enough to keep
  * ``memory_local``   — the process-local reference backend
  * ``memory_manager`` — policy in front of any store (scrub, cap, catch)

``from rugbrain.digest.memory import X`` keeps working for every existing
caller; the split is an implementation detail of the package, not a
contract change. See :mod:`rugbrain.digest.memory_types` for why mem0 is
a port rather than a dependency.
"""

from __future__ import annotations

from rugbrain.digest.memory_filter import should_remember
from rugbrain.digest.memory_local import InMemoryMemoryStore
from rugbrain.digest.memory_manager import MemoryManager
from rugbrain.digest.memory_types import (
    MAX_MEMORY_CHARS,
    Memory,
    MemoryStore,
    memory_id_for,
)

__all__ = [
    "MAX_MEMORY_CHARS",
    "InMemoryMemoryStore",
    "Memory",
    "MemoryManager",
    "MemoryStore",
    "memory_id_for",
    "should_remember",
]
