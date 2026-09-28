"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC
The typed outcome of one graph write.

Its own module because the result is part of the contract, not part of
the write: callers branch on it, ``DigestResult`` embeds it, and a test
asserts on it without ever opening a driver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GraphWriteResult:
    """Outcome of one episodic write. Never raises."""

    ok: bool
    nodes_created: int = 0
    relationships_created: int = 0
    entities_linked: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "nodes_created": self.nodes_created,
            "relationships_created": self.relationships_created,
            "entities_linked": self.entities_linked,
            "error": self.error,
        }
