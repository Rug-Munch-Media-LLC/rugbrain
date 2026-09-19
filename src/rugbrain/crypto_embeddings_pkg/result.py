"""Embedding result container."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EmbeddingResult:
    vector: list[float]
    dims: int
    model: str
    head: str
    metadata: dict[str, Any] = field(default_factory=dict)
    cached: bool = False
